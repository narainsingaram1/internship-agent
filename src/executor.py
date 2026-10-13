"""Playwright Form Execution Engine supporting Standard ATS, Workday, and iCIMS."""
import os
import re
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
        # Pre-execution: forcefully destroy any backdrops or cookie modals that intercept pointer events
        try:
            self.page.evaluate("""() => {
                document.querySelectorAll('[data-ui*="cookie"], [data-ui="backdrop"], [class*="backdrop"], #onetrust-banner-sdk').forEach(el => el.remove());
            }""")
        except Exception:
            pass

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

        # Try inside accessible iframes
        for iframe_sel in ["#icims_content_iframe", "iframe[name='icims_content_iframe']", "iframe[src*='icims']", "iframe"]:
            try:
                if self.page.locator(iframe_sel).count() > 0:
                    frame = self.page.frame_locator(iframe_sel)
                    frame_loc = frame.locator(selector).first
                    if frame_loc.count() > 0:
                        return frame_loc
            except Exception:
                pass

        return loc

    def _execute_single_action(self, action: FormFieldAction):
        """Execute one specific form interaction with fallbacks for Workday, Workable, and React comboboxes."""
        selector = action.selector
        loc = self._get_element_locator(selector)

        # Fallback for file inputs if specific random ID selector was generated
        if action.action_type == "upload_file":
            filepath = self._resolve_file_path(action.value.lower())
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File to upload does not exist: {filepath}")

            if loc.count() == 0 or not loc.is_visible():
                loc = self.page.locator("input[type='file']").first

            if loc.count() > 0:
                loc.set_input_files(filepath, timeout=5000)
                time.sleep(1)
                return

        if loc.count() == 0:
            # Fallback for radio or checkbox buttons whose dynamic IDs rotated
            if action.action_type in ["click_checkbox", "select_option"]:
                candidate_labels = []
                # Extract text inside parentheses, e.g. "Degree (Bachelors)" -> "Bachelors"
                m = re.search(r"\(([^)]+)\)", action.field_name)
                if m:
                    candidate_labels.append(m.group(1))
                if "->" in action.field_name:
                    candidate_labels.append(action.field_name.split("->")[-1].replace("Option:", "").strip())
                candidate_labels.append(action.field_name)

                for cl in candidate_labels:
                    lbl_loc = self.page.locator(f"label:has-text('{cl}')").first
                    if lbl_loc.count() > 0:
                        loc = lbl_loc
                        break

            if loc.count() == 0:
                raise ValueError(f"Element {selector} not found on page or iframe.")

        # Scroll into view
        try:
            loc.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass

        if action.action_type == "type_text":
            try:
                loc.click(force=True, timeout=1500)
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
            # 1. Check if a clickable radio/label exists with this text (e.g. Bachelors, Male, Yes)
            try:
                label_loc = self.page.locator(f"label:has-text('{action.value}')").first
                if label_loc.count() > 0 and label_loc.is_visible():
                    label_loc.click(force=True, timeout=1500)
                    return
            except Exception:
                pass

            # 2. Native select
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

            # 3. Workday / Workable / ARIA Combobox popup handling
            try:
                loc.click(force=True, timeout=2000)
                self.page.wait_for_timeout(400)
                option_locators = [
                    f"ul[role='listbox'] li:has-text('{action.value}')",
                    f"li[role='option']:has-text('{action.value}')",
                    f"div[role='option']:has-text('{action.value}')",
                    f"li:has-text('{action.value}')",
                    f"[data-automation-id='promptOption']:has-text('{action.value}')",
                    f"[role='option']:has-text('{action.value}')",
                    f"div:has-text('{action.value}')",
                ]
                for opt_sel in option_locators:
                    opt = self.page.locator(opt_sel).first
                    if opt.count() > 0 and opt.is_visible():
                        try:
                            opt.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass
                        opt.click(force=True, timeout=2000)
                        return
            except Exception as e:
                logger.debug(f"Combobox popup selection attempt failed: {e}")

            # 4. Fuzzy match native options
            try:
                options = loc.locator("option").all_inner_texts()
                matched = [o for o in options if action.value.lower() in o.lower()]
                if matched:
                    loc.select_option(label=matched[0], timeout=1500)
                    return
            except Exception:
                pass

            # 5. Type into autocomplete input if editable
            try:
                is_readonly = loc.get_attribute("readonly")
                if not is_readonly:
                    loc.fill(action.value, timeout=1500)
                    self.page.keyboard.press("Enter")
                    return
            except Exception:
                pass

            raise ValueError(f"Could not find matching option for '{action.value}'")

        elif action.action_type == "click_checkbox":
            if action.value.lower() in ["true", "yes", "1"]:
                try:
                    loc.check(force=True, timeout=2000)
                except Exception:
                    loc.click(force=True, timeout=2000)
            else:
                try:
                    loc.uncheck(force=True, timeout=2000)
                except Exception:
                    pass
