"""Terminal Interface and Human-In-The-Loop (HITL) Verification."""
import re
import time
from typing import Dict, Any, List
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from playwright.sync_api import Page

console = Console()


class HITLReviewer:
    """Manages the verification step before final submission."""

    def __init__(self, page: Page):
        self.page = page

    def display_plan_summary(
        self,
        job_meta: Dict[str, Any],
        execution_results: Dict[str, Any],
        questions_to_review: List[str],
    ):
        """Render a clean rich terminal overview of all filled form fields."""
        title = job_meta.get("title", "Internship Application")
        company = job_meta.get("company", "Company")
        platform = job_meta.get("platform", "generic").upper()

        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]{title}[/bold cyan] at [bold green]{company}[/bold green]\n"
                f"[dim]Platform: {platform} | URL: {job_meta.get('url')}[/dim]",
                title="🎯 [bold yellow]Application Review Verification[/bold yellow]",
                border_style="cyan",
            )
        )

        table = Table(title="Form Actions Executed", border_style="dim")
        table.add_column("Field", style="white", min_width=25)
        table.add_column("Action", style="cyan")
        table.add_column("Value / Input", style="green", max_width=50)
        table.add_column("Conf.", justify="right")

        for item in execution_results.get("succeeded", []):
            conf_str = f"{int(item.get('confidence', 1.0) * 100)}%"
            val = str(item.get("value", ""))
            # Truncate long essay text in table
            if len(val) > 48:
                val = val[:45] + "..."
            table.add_row(item.get("field"), item.get("type"), val, conf_str)

        for item in execution_results.get("failed", []):
            table.add_row(item.get("field"), "[bold red]FAILED[/bold red]", str(item.get("error"))[:40], "0%")

        console.print(table)

        if questions_to_review:
            console.print("\n[bold yellow]⚠️  Items flagged for your review:[/bold yellow]")
            for q in questions_to_review:
                console.print(f"  • [yellow]{q}[/yellow]")

    def prompt_human_action(self) -> str:
        """Present options to user: Submit, Takeover in browser, or Abort."""
        console.print("\n[bold white]What would you like to do?[/bold white]")
        console.print("  [bold green][S][/bold green]ubmit   - Click final submit button")
        console.print("  [bold cyan][T][/bold cyan]akeover  - Edit/Inspect live in the open browser window")
        console.print("  [bold red][A][/bold red]bort    - Discard and exit without submitting")

        choice = Prompt.ask("\nEnter choice", choices=["s", "t", "a", "S", "T", "A"], default="t").lower()

        if choice == "t":
            console.print("\n[bold cyan]🕹️  Browser Takeover Activated![/bold cyan]")
            console.print("The browser window is open. Review fields, solve any CAPTCHAs, or adjust answers.")
            sub_choice = Prompt.ask("When finished, press [bold green]Enter[/bold green] to submit, or type [bold red]'abort'[/bold red] to quit", default="")
            if sub_choice.strip().lower() == "abort":
                return "abort"
            return "submit"

        elif choice == "s":
            return "submit"
        else:
            return "abort"

    def submission_confirmed(self) -> bool:
        pattern = re.compile(
            r"^(?:thank you for (?:applying|submitting)|your application (?:has been |was |is )?"
            r"(?:successfully )?(?:submitted|received)|we have received your application|"
            r"application (?:submitted|received)|successfully applied)", re.I
        )
        for frame in self.page.frames:
            for text in frame.locator("h1:visible,h2:visible,h3:visible,p:visible,[role='status']:visible,[role='alert']:visible").all_inner_texts():
                if len(text) < 300 and pattern.search(text.strip()):
                    return True
        return False

    def submit_application(self, confirmation_timeout=5) -> bool:
        """Click an explicit final action once; success requires a receipt in the UI."""
        if self.submission_confirmed():
            return True
        candidates = []
        for frame in self.page.frames:
            if frame.locator("input[type='password']:visible, [aria-invalid='true']:visible, input:invalid:visible, select:invalid:visible, textarea:invalid:visible").count():
                return False
            next_buttons = frame.get_by_role("button", name=re.compile(r"^(next|continue|save and continue)$", re.I))
            if any(next_buttons.nth(i).is_visible() for i in range(next_buttons.count())):
                return False
            buttons = frame.get_by_role("button", name=re.compile(r"^(submit|submit application|send application|complete application|finish|apply)$", re.I))
            for i in range(buttons.count()):
                button = buttons.nth(i)
                if button.is_visible() and button.is_enabled():
                    if button.inner_text().strip().lower() == "apply" and not button.evaluate("el => el.type === 'submit' && Boolean(el.closest('form'))"):
                        continue
                    candidates.append(button)
        if len(candidates) != 1:
            console.print("[yellow]Final submit action is unavailable or ambiguous. Review the live browser.[/yellow]")
            return False
        candidates[0].click(timeout=5000)
        deadline = time.monotonic() + confirmation_timeout
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(200)
            if self.submission_confirmed():
                return True
        console.print("[yellow]Submission is unconfirmed. The browser remains open for review.[/yellow]")
        return False
