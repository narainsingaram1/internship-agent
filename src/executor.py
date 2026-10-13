"""Playwright Form Execution Engine supporting Standard ATS, Workday, and iCIMS."""
import os
import time
import logging
from typing import Dict, Any, List
from playwright.sync_api import Page, TimeoutError

from .llm.prompts import FormFillPlan, FormFieldAction

logger = logging.getLogger(__name__)


class FormExecutor:
    """Executes actions from a FormFillPlan on a live Playwright page, supporting iframes and custom comboboxes."""

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

    def _get_element_locator(self, selector: str):
        """Find element on main page or inside iCIMS iframe if present."""
        loc = self.page.locator(selector).first
        if loc.count() > 0:
            return loc

        # Try inside iCIMS iframe
        for iframe_sel in ["#icims_content_iframe", "iframe[name='icims_content_iframe']", "iframe[src*='icims']"]:
            if self.page.locator(iframe_sel).count() > 0:
                frame = self.page.frame_locator(iframe_sel)
                frame_loc = frame.locator(selector).first
                if frame_loc.count() > 0:
                    return frame_loc

        return loc

    def _execute_single_action(self, action: FormFieldAction):
        """Execute one specific form interaction with fallbacks for Workday and React comboboxes."""
        selector = action.selector
        loc = self._get_element_locator(selector)

        if loc.count() == 0:
            raise ValueError(f"Element {selector} not found on page or iframe.")

        # Scroll into view
        try:
            loc.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        if action.action_type == "type_text":
            try:
                loc.click(timeout=1500)
                loc.fill(action.value, timeout=2000)
            except Exception:
                # Fallback JavaScript evaluation for controlled inputs
                self.page.evaluate(
                    """({ sel, val }) => {
                        let el = document.querySelector(sel);
                        if (!el) {
                            const iframe = document.querySelector('#icims_content_iframe');
                            if (iframe && iframe.contentDocument) el = iframe.contentDocument.querySelector(sel);
                        }
                        if (el) {
                            el.value = val;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""",
                    {"sel": selector, "val": action.value},
                )

        elif action.action_type == "select_option":
            # 1. Native select
            try:
                loc.select_option(label=action.value, timeout=1500)
                return
            except Exception:
                pass

            try:
                loc.select_option(value=action.value, timeout=1500)
                return
            except Exception:
                pass

            # 2. Workday / ARIA Combobox popup handling
            try:
                loc.click(timeout=2000)
                self.page.wait_for_timeout(500)
                # Look for matching option in popup listbox
                option_locators = [
                    f"div[role='option']:has-text('{action.value}')",
                    f"li[role='option']:has-text('{action.value}')",
                    f"[data-automation-id='promptOption']:has-text('{action.value}')",
                    f"div:has-text('{action.value}')",
                ]
                for opt_sel in option_locators:
                    opt = self.page.locator(opt_sel).first
                    if opt.count() > 0 and opt.is_visible():
                        opt.click(timeout=2000)
                        return
            except Exception as e:
                logger.debug(f"Combobox popup selection attempt failed: {e}")

            # 3. Fuzzy match native options
            try:
                options = loc.locator("option").all_inner_texts()
                matched = [o for o in options if action.value.lower() in o.lower()]
                if matched:
                    loc.select_option(label=matched[0], timeout=1500)
                    return
            except Exception:
                pass

            raise ValueError(f"Could not find matching option for '{action.value}'")

        elif action.action_type == "click_checkbox":
            if action.value.lower() in ["true", "yes", "1"]:
                try:
                    loc.check(timeout=2000)
                except Exception:
                    loc.click(timeout=2000)
            else:
                try:
                    loc.uncheck(timeout=2000)
                except Exception:
                    pass

        elif action.action_type == "upload_file":
            filepath = self._resolve_file_path(action.value.lower())
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File to upload does not exist: {filepath}")

            # If selector is a drop-zone wrapper, find nested file input
            if loc.locator("input[type='file']").count() > 0:
                loc = loc.locator("input[type='file']").first

            loc.set_input_files(filepath, timeout=5000)
            time.sleep(1)
