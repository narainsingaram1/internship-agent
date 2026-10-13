"""Playwright Form Execution Engine supporting Standard ATS, Workday, and iCIMS."""
import os
import re
import time
import logging
from typing import Dict, Any, List, Optional
from playwright.sync_api import Page, TimeoutError

from .llm.prompts import FormFillPlan, FormFieldAction

logger = logging.getLogger(__name__)


class OptionMatcher:
    """Intelligently matches candidate values & aliases to available dropdown options on the live page."""

    TOPIC_ALIASES = {
        "school": [
            "georgia institute of technology", "georgia tech", "georgia inst of tech", "gt", "gatech"
        ],
        "university": [
            "georgia institute of technology", "georgia tech", "georgia inst of tech", "gt", "gatech"
        ],
        "college": [
            "georgia institute of technology", "georgia tech", "georgia inst of tech", "gt", "gatech"
        ],
        "python": [
            "full proficiency", "fully proficient", "expert", "fluent", "advanced", "proficient", "intermediate"
        ],
        "proficiency": [
            "full proficiency", "fully proficient", "expert", "fluent", "advanced", "proficient", "intermediate"
        ],
        "major": [
            "computer science", "cs", "computer science and engineering", "software engineering", "computing", "information technology"
        ],
        "subject": [
            "computer science", "cs", "computer science and engineering", "software engineering", "computing", "information technology"
        ],
        "degree": [
            "bachelor of science", "bachelors", "bachelor", "b.s.", "bs", "undergraduate", "bachelor degree"
        ],
        "gender": [
            "prefer not to say", "decline to self identify", "decline to answer", "i do not wish to disclose", "prefer not to disclose", "male"
        ],
        "source": [
            "linkedin", "university career event", "university career site", "other", "company website", "job board"
        ],
        "referral": [
            "linkedin", "university career event", "university career site", "other", "company website"
        ],
        "country": [
            "united states", "usa", "united states of america", "us"
        ],
        "work_auth": [
            "yes", "authorized", "us citizen", "authorized without restriction"
        ],
        "sponsorship": [
            "no", "will not require sponsorship", "no sponsorship required"
        ],
    }

    @classmethod
    def find_best_option(cls, target_value: str, field_name: str, available_options: List[str]) -> Optional[str]:
        if not available_options:
            return None

        clean_opts = [o.strip() for o in available_options if o and o.strip()]
        target_lower = target_value.lower().strip()
        field_lower = field_name.lower().strip()

        # 1. Exact case-insensitive match
        for o in clean_opts:
            if o.lower() == target_lower:
                return o

        # 2. Topic-aware aliases
        topic_candidates = [target_lower]
        for topic, aliases in cls.TOPIC_ALIASES.items():
            if topic in field_lower or topic in target_lower:
                topic_candidates.extend(aliases)
        topic_candidates = list(dict.fromkeys(topic_candidates))

        for cand in topic_candidates:
            for o in clean_opts:
                if o.lower() == cand:
                    return o
            for o in clean_opts:
                if cand in o.lower() or o.lower() in cand:
                    return o

        # 3. Substring & Token Overlap scoring
        target_tokens = set(re.findall(r"\w+", target_lower))
        best_opt = None
        max_overlap = 0

        for o in clean_opts:
            o_tokens = set(re.findall(r"\w+", o.lower()))
            overlap = len(target_tokens & o_tokens)
            if overlap > max_overlap:
                max_overlap = overlap
                best_opt = o

        if max_overlap > 0:
            return best_opt

        # 4. Fallback to 'Other' if candidate value genuinely not present in list
        for o in clean_opts:
            if o.lower() in ["other", "other / not listed", "not listed"]:
                return o

        return None


class FormExecutor:
    """Executes actions from a FormFillPlan on a live Playwright page, supporting iframes and custom comboboxes."""

    def __init__(self, page: Page, profile: Dict[str, Any], project_root: str = "."):
        self.page = page
        self.profile = profile
        self.project_root = project_root
        self.executed_options: Dict[str, str] = {}

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

            # Dependent Question Guard:
            # If a field asks "If you have selected 'Other' ... enter your school below",
            # check whether the school dropdown previously selected an actual university (and NOT 'Other')!
            if any(k in action.field_name.lower() for k in ["if you have selected 'other'", "if other", "selected 'other'"]):
                school_choice = self.executed_options.get("school", "")
                if school_choice and school_choice.lower() != "other":
                    logger.info(f"Skipping '{action.field_name}' because '{school_choice}' was already selected in the dropdown.")
                    results["skipped"].append({
                        "field": action.field_name,
                        "reason": f"School was already selected as '{school_choice}' in the main dropdown above."
                    })
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
        """Execute one specific form interaction with intelligent dynamic option matching and fallbacks."""
        # 1. Close any open listboxes or dangling popups from previous interactions
        try:
            open_lbs = self.page.locator('[role="listbox"]:visible, ul[role="listbox"]:visible')
            if open_lbs.count() > 0:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(150)
        except Exception:
            pass

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
            # 1. Check if a clickable radio/label exists with this exact text
            try:
                label_loc = self.page.locator(f"label:has-text('{action.value}')").first
                if label_loc.count() > 0 and label_loc.is_visible():
                    label_loc.click(force=True, timeout=1500)
                    return
            except Exception:
                pass

            # 2. Native <select> handling with dynamic OptionMatcher
            try:
                native_opts = loc.locator("option").all_inner_texts()
                if native_opts:
                    best_match = OptionMatcher.find_best_option(action.value, action.field_name, native_opts)
                    if best_match:
                        loc.select_option(label=best_match, timeout=1500)
                        self._record_executed_option(action.field_name, best_match)
                        return
            except Exception:
                pass

            # 3. Workday / Workable / ARIA Combobox popup handling with dynamic OptionMatcher
            try:
                loc.click(force=True, timeout=2000)
                self.page.wait_for_timeout(350)

                # Collect all visible options in popup listbox
                listbox_id = loc.get_attribute("aria-controls") or loc.get_attribute("aria-owns")
                if listbox_id:
                    box = self.page.locator(f"#{listbox_id}")
                else:
                    box = self.page.locator("ul[role='listbox'], [role='listbox']").first

                avail = box.locator("li, [role='option']").all_inner_texts()
                best_opt = OptionMatcher.find_best_option(action.value, action.field_name, avail)
                target_to_click = best_opt or action.value

                # Search within scoped listbox using exact text first, then partial match
                opt = box.locator(f"li:text-is('{target_to_click}'), [role='option']:text-is('{target_to_click}')").first
                if opt.count() == 0:
                    opt = box.locator(f"li:has-text('{target_to_click}'), [role='option']:has-text('{target_to_click}')").first

                if opt.count() > 0:
                    try:
                        opt.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    opt.click(force=True, timeout=2000)
                    self._record_executed_option(action.field_name, target_to_click)

                # Ensure popup is cleanly closed and input is blurred
                self.page.wait_for_timeout(200)
                if listbox_id and self.page.locator(f"#{listbox_id}").is_visible():
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(150)
                try:
                    loc.evaluate("el => el.blur()")
                except Exception:
                    pass
                return
            except Exception as e:
                logger.debug(f"Combobox popup selection attempt failed: {e}")

            # 4. Type into autocomplete input if editable
            try:
                is_readonly = loc.get_attribute("readonly")
                if not is_readonly:
                    loc.fill(action.value, timeout=1500)
                    self.page.keyboard.press("Enter")
                    self._record_executed_option(action.field_name, action.value)
                    return
            except Exception:
                pass

            raise ValueError(f"Could not find matching option for '{action.value}'")

        elif action.action_type == "click_checkbox":
            should_check = action.value.lower() in ["true", "yes", "1"]
            try:
                if should_check:
                    loc.check(force=True, timeout=1000)
                else:
                    loc.uncheck(force=True, timeout=1000)
            except Exception:
                # For custom stylized checkboxes/radios, click enclosing label or container
                parent_lbl = loc.locator("xpath=ancestor::label").first
                if parent_lbl.count() > 0:
                    parent_lbl.click(force=True, timeout=1500)
                else:
                    loc.click(force=True, timeout=1500)

    def _record_executed_option(self, field_name: str, chosen_value: str):
        """Record the selected value to assist dependent questions (e.g. school vs if-other)."""
        fn_lower = field_name.lower()
        if any(k in fn_lower for k in ["school", "university", "college", "attending"]):
            self.executed_options["school"] = chosen_value
        elif "python" in fn_lower:
            self.executed_options["python"] = chosen_value
        elif "degree" in fn_lower:
            self.executed_options["degree"] = chosen_value
        elif "location" in fn_lower:
            self.executed_options["location"] = chosen_value
