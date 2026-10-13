"""Playwright Form Execution Engine."""
import os
import time
import logging
from typing import Dict, Any, List
from playwright.sync_api import Page, TimeoutError

from .llm.prompts import FormFillPlan, FormFieldAction

logger = logging.getLogger(__name__)


class FormExecutor:
    """Executes actions from a FormFillPlan on a live Playwright page."""

    def __init__(self, page: Page, profile: Dict[str, Any], project_root: str = "."):
        self.page = page
        self.profile = profile
        self.project_root = project_root

    def _resolve_file_path(self, file_type: str) -> str:
        """Resolve absolute file path for resume or transcript."""
        docs = self.profile.get("documents", {})
        if file_type == "resume":
            rel_path = docs.get("resume_path", "assets/resume.pdf")
        elif file_type == "transcript":
            rel_path = docs.get("transcript_path", "assets/transcript.pdf")
        else:
            rel_path = docs.get("resume_path", "assets/resume.pdf")

        if os.path.isabs(rel_path):
            return rel_path
        return os.path.join(self.project_root, rel_path)

    def execute_plan(self, plan: FormFillPlan) -> Dict[str, Any]:
        """Execute all actions specified in the FormFillPlan."""
        results = {
            "succeeded": [],
            "failed": [],
            "skipped": [],
        }

        for action in plan.actions:
            if action.action_type == "skip" or not action.value:
                results["skipped"].append({"field": action.field_name, "reason": action.reasoning})
                continue

            try:
                self._execute_single_action(action)
                results["succeeded"].append({
                    "field": action.field_name,
                    "type": action.action_type,
                    "value": action.value,
                    "confidence": action.confidence,
                })
            except Exception as e:
                logger.warning(f"Could not execute action on {action.field_name} ({action.selector}): {e}")
                results["failed"].append({
                    "field": action.field_name,
                    "selector": action.selector,
                    "error": str(e),
                })

        return results

    def _execute_single_action(self, action: FormFieldAction):
        """Execute one specific form interaction."""
        selector = action.selector
        loc = self.page.locator(selector).first

        if loc.count() == 0:
            raise ValueError(f"Element {selector} not found on page.")

        # Scroll into view
        loc.scroll_into_view_if_needed(timeout=3000)

        if action.action_type == "type_text":
            # Clear existing value and type
            try:
                loc.click(timeout=2000)
                loc.fill(action.value, timeout=2000)
            except Exception:
                # Fallback JavaScript evaluation for React-controlled inputs
                self.page.evaluate(
                    """({ sel, val }) => {
                        const el = document.querySelector(sel);
                        if (el) {
                            el.value = val;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""",
                    {"sel": selector, "val": action.value},
                )

        elif action.action_type == "select_option":
            try:
                # Try selecting by label, then by value
                loc.select_option(label=action.value, timeout=2000)
            except Exception:
                try:
                    loc.select_option(value=action.value, timeout=2000)
                except Exception:
                    # Partial match
                    options = loc.locator("option").all_inner_texts()
                    matched = [o for o in options if action.value.lower() in o.lower()]
                    if matched:
                        loc.select_option(label=matched[0], timeout=2000)
                    else:
                        raise ValueError(f"Could not find matching option for '{action.value}'")

        elif action.action_type == "click_checkbox":
            if action.value.lower() in ["true", "yes", "1"]:
                loc.check(timeout=2000)
            else:
                loc.uncheck(timeout=2000)

        elif action.action_type == "upload_file":
            filepath = self._resolve_file_path(action.value.lower())
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File to upload does not exist: {filepath}")
            loc.set_input_files(filepath, timeout=5000)
            time.sleep(1)  # Allow upload animation to finish
