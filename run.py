#!/usr/bin/env python3
"""Main Entry Point for Autonomous Internship Application Agent."""
import os
import sys
import yaml
import logging
from rich.console import Console

from src.browser import BrowserSession
from src.detector import ATSDetector
from src.parsers import GreenhouseParser, LeverParser, WorkdayParser, ICIMSParser, WorkableParser, GenericParser
from src.llm import LLMClient
from src.memory import MemoryStore
from src.resolver import TwoTierResolver
from src.executor import FormExecutor
from src.controller import ApplicationController
from src.cli import HITLReviewer
from src.tracker import ApplicationTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("internship_agent")
console = Console()


def load_yaml(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def main():
    console.print(
        "\n[bold green]🚀 Autonomous Internship Application Agent[/bold green] [dim](100% Free Frontier Model Edition)[/dim]\n"
    )

    # 1. Load Configurations
    root_dir = os.path.dirname(os.path.abspath(__file__))
    profile_path = os.path.join(root_dir, "config", "profile.yaml")
    example_profile_path = os.path.join(root_dir, "config", "profile.example.yaml")

    if not os.path.exists(profile_path):
        console.print(f"[yellow]⚠️  Notice: 'config/profile.yaml' not found. Using 'config/profile.example.yaml'.[/yellow]")
        console.print(f"[dim]Tip: Copy 'config/profile.example.yaml' to 'config/profile.yaml' and edit with your info![/dim]\n")
        profile = load_yaml(example_profile_path)
    else:
        profile = load_yaml(profile_path)

    settings = load_yaml(os.path.join(root_dir, "config", "settings.yaml"))
    browser_cfg = settings.get("browser", {})
    llm_cfg = settings.get("llm", {})

    # 2. Get Job URL
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        from rich.prompt import Prompt
        target_url = Prompt.ask("[bold cyan]Enter Internship Application URL[/bold cyan]").strip()

    if not target_url.startswith("http"):
        console.print("[bold red]Error: Invalid URL provided.[/bold red]")
        sys.exit(1)

    # 3. Detect Platform
    platform, meta = ATSDetector.identify(target_url)
    meta["url"] = target_url
    console.print(f"Detected Platform: [bold magenta]{platform.upper()}[/bold magenta]")

    # 4. Initialize Components
    session = BrowserSession(
        headless=browser_cfg.get("headless", False),
        slow_mo=browser_cfg.get("slow_mo_ms", 30),
    )
    llm = LLMClient(
        base_url=llm_cfg.get("openrouter_base_url", "https://openrouter.ai/api/v1"),
        default_model=llm_cfg.get("model", "google/gemini-2.0-flash-exp:free"),
        fallback_model=llm_cfg.get("fallback_model", "meta-llama/llama-3.3-70b-instruct:free"),
    )
    tracker = ApplicationTracker(os.path.join(root_dir, "data", "applications.json"))

    try:
        page = session.navigate(target_url)

        # 5. Select Parser
        if platform == "greenhouse":
            parser = GreenhouseParser()
        elif platform == "lever":
            parser = LeverParser()
        elif platform == "workday":
            parser = WorkdayParser()
        elif platform == "icims":
            parser = ICIMSParser()
        elif platform == "workable":
            parser = WorkableParser()
        else:
            parser = GenericParser()

        job_meta = parser.extract_job_metadata(page)
        job_meta.update(meta)
        console.print(f"Role: [bold white]{job_meta.get('title', 'Unknown Title')}[/bold white]")
        if job_meta.get("company"):
            console.print(f"Company: [bold white]{job_meta.get('company')}[/bold white]")

        resolver = TwoTierResolver(MemoryStore(os.path.join(root_dir, "data", "memory_store.json")), llm)
        executor = FormExecutor(page, profile, project_root=root_dir)
        controller = ApplicationController(page, parser, resolver, executor, log=logger.info)
        plan, exec_results = controller.run(profile, job_meta)

        # 9. Human-In-The-Loop Verification
        reviewer = HITLReviewer(page)
        reviewer.display_plan_summary(job_meta, exec_results, plan.questions_to_review)

        user_action = reviewer.prompt_human_action()

        if user_action == "submit":
            reviewer.submit_application()
            tracker.record_application(
                url=target_url,
                company=job_meta.get("company", ""),
                title=job_meta.get("title", ""),
                status="Submitted",
                fields_filled=len(exec_results.get("succeeded", [])),
                notes="Successfully verified and submitted by user.",
            )
            console.print("\n[bold green]🎉 Application Successfully Submitted and Logged![/bold green]\n")
        else:
            tracker.record_application(
                url=target_url,
                company=job_meta.get("company", ""),
                title=job_meta.get("title", ""),
                status="Aborted",
                fields_filled=len(exec_results.get("succeeded", [])),
                notes="Application aborted during verification.",
            )
            console.print("\n[bold red]❌ Application was aborted. No submission made.[/bold red]\n")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]Error during execution: {e}[/bold red]")
        import traceback
        traceback.print_exc()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
