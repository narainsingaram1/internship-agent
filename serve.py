#!/usr/bin/env python3
"""Launch the Minimalist Web Dashboard for AutoApply AI."""
import os
import sys
import webbrowser
import uvicorn
from threading import Timer

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def open_browser():
    webbrowser.open("http://localhost:8000")


if __name__ == "__main__":
    print("\n🚀 Starting AutoApply AI Minimalist Web Dashboard...")
    print("📍 Local URL: http://localhost:8000")
    print("💡 Press Ctrl+C in this terminal to stop the server.\n")

    # Open browser automatically after 1 second
    Timer(1.2, open_browser).start()

    uvicorn.run("web.server:app", host="127.0.0.1", port=8000, reload=False, log_level="warning")
