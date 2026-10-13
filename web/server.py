"""FastAPI Backend for Minimalist Web Application."""
import os
import sys
import json
import yaml
import time
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from threading import Thread

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Ensure root directory is on PYTHONPATH
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.browser import BrowserSession
from src.detector import ATSDetector
from src.parsers import GreenhouseParser, LeverParser, GenericParser
from src.llm import LLMClient
from src.executor import FormExecutor
from src.tracker import ApplicationTracker

load_dotenv(os.path.join(ROOT_DIR, ".env"))

app = FastAPI(title="AutoApply AI Engine", version="2.0.0")

# Static files setup
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
DATA_DIR = os.path.join(ROOT_DIR, "data")
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.yaml")
EXAMPLE_PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.example.yaml")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.yaml")
SCREENSHOT_PATH = os.path.join(DATA_DIR, "preview.png")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# Application state
class EngineSession:
    def __init__(self):
        self.state = "idle"  # idle, running, review, submitted, error
        self.browser_session: Optional[BrowserSession] = None
        self.page = None
        self.job_meta = {}
        self.plan = None
        self.execution_results = {}
        self.clients = set()
        self.loop = None

    async def broadcast(self, data: dict):
        dead_clients = set()
        for client in self.clients:
            try:
                await client.send_json(data)
            except Exception:
                dead_clients.add(client)
        self.clients -= dead_clients

    def log(self, message: str, level: str = "info"):
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast({"type": "log", "message": message, "level": level, "timestamp": datetime.now().strftime("%H:%M:%S")}),
                self.loop
            )

    def set_step(self, step_idx: int, label: str):
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast({"type": "step", "step": step_idx, "label": label}),
                self.loop
            )

engine = EngineSession()


def load_yaml(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_yaml(path: str, data: dict):
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def get_profile() -> dict:
    if os.path.exists(PROFILE_PATH):
        return load_yaml(PROFILE_PATH)
    return load_yaml(EXAMPLE_PROFILE_PATH)


# --- REST ENDPOINTS ---

@app.get("/api/status")
async def get_status():
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    settings = load_yaml(SETTINGS_PATH)
    active_model = settings.get("llm", {}).get("model", "google/gemini-2.0-flash-exp:free")
    
    return {
        "state": engine.state,
        "has_api_key": bool(api_key),
        "model": active_model,
        "job_meta": engine.job_meta,
    }


@app.get("/api/profile")
async def get_profile_data():
    return get_profile()


@app.post("/api/profile")
async def update_profile(profile_data: dict):
    save_yaml(PROFILE_PATH, profile_data)
    return {"status": "saved"}


@app.post("/api/upload-resume")
async def upload_resume(file: UploadFile = File(...)):
    dest_path = os.path.join(ROOT_DIR, "assets", "resume.pdf")
    os.makedirs(os.path.join(ROOT_DIR, "assets"), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(await file.read())
    return {"status": "uploaded", "filename": file.filename}


@app.get("/api/history")
async def get_history():
    tracker = ApplicationTracker(os.path.join(DATA_DIR, "applications.json"))
    return tracker.load_applications()


@app.get("/api/screenshot")
async def get_screenshot():
    if os.path.exists(SCREENSHOT_PATH):
        return FileResponse(SCREENSHOT_PATH, media_type="image/png")
    raise HTTPException(status_code=404, detail="No screenshot available")


# Background worker for automation
def run_autofill_worker(target_url: str, headed: bool):
    engine.state = "running"
    engine.set_step(1, "Initializing Browser Session")
    engine.log(f"Starting browser session (Headed: {headed})...")

    try:
        platform, meta = ATSDetector.identify(target_url)
        meta["url"] = target_url
        engine.job_meta = meta

        engine.set_step(2, f"Targeting {platform.upper()} Form")
        engine.log(f"Detected ATS platform: {platform.upper()}")

        session = BrowserSession(headless=not headed, slow_mo=30)
        engine.browser_session = session
        page = session.navigate(target_url)
        engine.page = page

        # Parser selection
        if platform == "greenhouse":
            parser = GreenhouseParser()
        elif platform == "lever":
            parser = LeverParser()
        else:
            parser = GenericParser()

        job_meta = parser.extract_job_metadata(page)
        job_meta.update(meta)
        engine.job_meta = job_meta
        engine.log(f"Role: {job_meta.get('title', 'Unknown')} at {job_meta.get('company', 'Unknown')}")

        engine.set_step(3, "Extracting Interactive Form Elements")
        fields = parser.extract_fields(page)
        if not fields:
            fields = GenericParser().extract_fields(page)
        engine.log(f"Found {len(fields)} interactive fields.")

        engine.set_step(4, "Mapping Fields via OpenRouter AI")
        engine.log("Querying OpenRouter Free Tier model for structured mapping...")
        profile = get_profile()
        llm = LLMClient()
        plan = llm.generate_fill_plan(fields, profile, job_meta)
        engine.plan = plan

        engine.set_step(5, "Autofilling Form Fields")
        engine.log("Executing autofill actions and attaching PDF resume...")
        executor = FormExecutor(page, profile, project_root=ROOT_DIR)
        exec_results = executor.execute_plan(plan)
        engine.execution_results = exec_results

        # Capture preview screenshot
        page.screenshot(path=SCREENSHOT_PATH)
        engine.log("Autofill complete! Halting for human verification.")

        engine.state = "review"
        if engine.loop:
            asyncio.run_coroutine_threadsafe(
                engine.broadcast({
                    "type": "review_ready",
                    "job_meta": engine.job_meta,
                    "plan": plan.dict(),
                    "results": exec_results,
                    "has_screenshot": True,
                }),
                engine.loop
            )

    except Exception as e:
        engine.state = "error"
        engine.log(f"Error during autofill: {str(e)}", level="error")
        if engine.loop:
            asyncio.run_coroutine_threadsafe(
                engine.broadcast({"type": "error", "message": str(e)}),
                engine.loop
            )


@app.post("/api/apply/start")
async def start_application(payload: dict):
    url = payload.get("url", "").strip()
    headed = payload.get("headed", True)

    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    engine.loop = asyncio.get_event_loop()
    Thread(target=run_autofill_worker, args=(url, headed), daemon=True).start()
    return {"status": "started", "url": url}


@app.post("/api/apply/submit")
async def submit_application():
    if not engine.page:
        raise HTTPException(status_code=400, detail="No active application session")

    try:
        from src.cli import HITLReviewer
        reviewer = HITLReviewer(engine.page)
        reviewer.submit_application()

        tracker = ApplicationTracker(os.path.join(DATA_DIR, "applications.json"))
        tracker.record_application(
            url=engine.job_meta.get("url", ""),
            company=engine.job_meta.get("company", ""),
            title=engine.job_meta.get("title", ""),
            status="Submitted",
            fields_filled=len(engine.execution_results.get("succeeded", [])),
            notes="Submitted via Minimalist Web UI",
        )

        if engine.browser_session:
            engine.browser_session.stop()

        engine.state = "submitted"
        await engine.broadcast({"type": "completed", "status": "submitted"})
        return {"status": "submitted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/apply/abort")
async def abort_application():
    if engine.browser_session:
        engine.browser_session.stop()

    engine.state = "idle"
    tracker = ApplicationTracker(os.path.join(DATA_DIR, "applications.json"))
    tracker.record_application(
        url=engine.job_meta.get("url", ""),
        company=engine.job_meta.get("company", ""),
        title=engine.job_meta.get("title", ""),
        status="Aborted",
        fields_filled=0,
        notes="Aborted by user in Web UI",
    )
    await engine.broadcast({"type": "completed", "status": "aborted"})
    return {"status": "aborted"}


# WebSocket for real-time log streaming
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    engine.clients.add(websocket)
    engine.loop = asyncio.get_event_loop()

    # Send current state
    await websocket.send_json({
        "type": "state",
        "state": engine.state,
        "job_meta": engine.job_meta,
    })

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        engine.clients.remove(websocket)


# Serve Static UI
@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.server:app", host="127.0.0.1", port=8000, reload=False)
