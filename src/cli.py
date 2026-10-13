"""Terminal Interface and Human-In-The-Loop (HITL) Verification."""
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

    def submit_application(self) -> bool:
        """Locate and click the final submit button."""
        console.print("[dim]Attempting submission...[/dim]")
        submit_selectors = [
            "input[type='submit']",
            "button[type='submit']",
            "#submit_app",
            "button:has-text('Submit Application')",
            "button:has-text('Submit')",
            "button:has-text('Apply')",
        ]

        for sel in submit_selectors:
            loc = self.page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                try:
                    loc.click(timeout=5000)
                    console.print("[bold green]✅ Successfully clicked Submit button![/bold green]")
                    self.page.wait_for_timeout(3000)
                    return True
                except Exception as e:
                    console.print(f"[yellow]Failed clicking {sel}: {e}[/yellow]")

        console.print("[yellow]Could not find visible submit button automatically. Please click it in the browser![/yellow]")
        Prompt.ask("Press Enter after clicking submit in the browser...")
        return True
