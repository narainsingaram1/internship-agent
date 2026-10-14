"""Bounded observe/fill/verify loop shared by the web and terminal entrypoints."""
import json
import re
import time

from .llm.prompts import FormFillPlan
from .parsers import GenericParser
from .parsers import WorkdayParser, GreenhouseParser, LeverParser, ICIMSParser, WorkableParser
from .detector import ATSDetector
from .navigation import ApplicationNavigator


class ApplicationController:
    def __init__(self, page, parser, resolver, executor, log=None, max_passes=20, transition_timeout=5):
        self.page = page
        self.parser = parser
        self.resolver = resolver
        self.executor = executor
        self.log = log or (lambda message: None)
        self.max_passes = max_passes
        self.transition_timeout = transition_timeout
        self.navigator = ApplicationNavigator(page, getattr(resolver, "llm", None), self.log,
                                               timeout=transition_timeout)
        self.surface = page
        self._completed = {}

    def sync_navigation(self, job_meta):
        self.page = self.navigator.page
        self.surface = self.navigator.form_frame or self.page
        self.executor.page = self.page
        self.executor.active_frame = self.navigator.form_frame
        platform, _ = ATSDetector.identify(self.surface.url)
        parsers = {"workday": WorkdayParser, "greenhouse": GreenhouseParser,
                   "lever": LeverParser, "icims": ICIMSParser, "workable": WorkableParser}
        if platform in parsers and platform != self.parser.platform_name:
            self.parser = parsers[platform]()
        job_meta["application_url"] = self.page.url
        job_meta["platform"] = self.parser.platform_name

    @staticmethod
    def field_key(field):
        return json.dumps({k: field.get(k) for k in (
            "selector", "label", "name", "type", "options"
        )}, sort_keys=True)

    def observe(self, prepare=False):
        platform = self.parser.platform_name
        if platform in {"generic", "workday", "workable", "icims"}:
            fields = self.parser.extract_fields(self.surface, prepare=prepare)
        else:
            fields = self.parser.extract_fields(self.surface)
        if not fields:
            fields = GenericParser().extract_fields(self.surface, prepare=prepare)
        active = []
        for field in fields:
            try:
                loc = self.executor._get_element_locator(field["selector"])
                visible_label = False
                if field.get("type") in {"radio", "checkbox"} and loc.count():
                    visible_label = loc.evaluate("""el => {
                        const label = el.closest('label') || (el.id &&
                            el.ownerDocument.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
                        return Boolean(label && label.getClientRects().length);
                    }""")
                if loc.count() and loc.is_enabled() and (
                    loc.is_visible() or visible_label or field.get("type") == "file"
                ):
                    active.append(field)
            except Exception:
                continue
        return active

    def fingerprint(self, fields):
        headings = self.surface.locator("h1:visible, h2:visible, legend:visible, [aria-current='step']:visible").all_inner_texts()
        return (self.surface.url, tuple(headings), tuple(sorted(self.field_key(f) for f in fields)))

    def next_button(self):
        # Never infer final submission from a generic type=submit or Apply button.
        buttons = self.surface.get_by_role("button", name=re.compile(
            r"^(next|save and continue|continue)$", re.I
        ))
        candidates = [buttons.nth(i) for i in range(buttons.count())
                      if buttons.nth(i).is_visible() and buttons.nth(i).is_enabled()]
        return candidates[0] if len(candidates) == 1 else None

    def validation_errors(self):
        return self.surface.locator(
            "[aria-invalid='true']:visible, [role='alert']:visible"
        ).count() > 0

    def run(self, profile, job_meta):
        results = {"succeeded": [], "failed": [], "skipped": []}
        actions, questions = [], []
        attempted = set()
        reached_form = self.navigator.ensure_form()
        self.sync_navigation(job_meta)
        if not reached_form:
            self.log(self.navigator.reason)
            results["navigation"] = self.navigator.stats
            return FormFillPlan(questions_to_review=[self.navigator.reason], summary=self.navigator.reason), results
        # Navigation owns Apply transitions; extraction must not click Apply again.
        fields = self.observe()
        step_context = self.fingerprint(fields)[:2]
        attempted = set(self._completed.get(step_context, set()))
        visited_steps = {self.fingerprint(fields)}
        reason = "Review the completed fields before submission."
        for pass_index in range(self.max_passes):
            # Authentication is a handoff, never another form-mapping request.
            if self.surface.locator("input[type='password']:visible").count():
                reason = "Login required. Sign in in the live browser before continuing."
                break
            if not fields:
                if self.navigator.ensure_form():
                    self.sync_navigation(job_meta)
                    fields = self.observe()
                    attempted.clear()
                    continue
                self.sync_navigation(job_meta)
                reason = self.navigator.reason
                break
            pending = [f for f in fields if self.field_key(f) not in attempted]
            if pending:
                self.log(f"Pass {pass_index + 1}: resolving {len(pending)} new or changed fields.")
                attempted.update(self.field_key(f) for f in pending)
                plan = self.resolver.resolve_form(pending, profile, job_meta)
                actions.extend(plan.actions)
                questions.extend(plan.questions_to_review)
                batch = self.executor.execute_plan(plan)
                successful_selectors = {action.selector for action in plan.actions
                                        if action.field_name in {r["field"] for r in batch["succeeded"]}}
                completed = self._completed.setdefault(self.fingerprint(fields)[:2], set())
                completed.update(self.field_key(f) for f in pending if f["selector"] in successful_selectors)
                for key in ("succeeded", "failed", "skipped"):
                    results[key].extend(batch[key])
                if batch["failed"]:
                    reason = "Some fields could not be verified. Review them in the live browser."
                    questions.extend(f"{f['field']}: {f['error']}" for f in batch["failed"])
                    break
                # Re-read after dependent questions have had time to mount.
                self.page.wait_for_timeout(300)
                fields = self.observe()
                continue
            if self.validation_errors():
                reason = "The page reports a validation error. Review the live form."
                break
            next_button = self.next_button()
            if next_button is None:
                snapshot = self.navigator.observe()
                if any(re.search(r"submit|finish|complete application", c["label"], re.I)
                       for c in snapshot["controls"]):
                    break
                if not snapshot["controls"]:
                    break
                action, control_id = self.navigator.choose(snapshot, "advance to the next application step; do not submit")
                if action != "click" or not self.navigator.click(control_id, snapshot):
                    reason = self.navigator.reason or "Review the remaining application steps."
                    break
                self.sync_navigation(job_meta)
                fields = self.observe()
                attempted.clear()
                continue
            before = self.fingerprint(fields)
            next_button.click(timeout=3000)
            deadline = time.monotonic() + self.transition_timeout
            changed = False
            while time.monotonic() < deadline:
                self.page.wait_for_timeout(250)
                fields = self.observe()
                after = self.fingerprint(fields)
                if self.validation_errors():
                    break
                if after != before:
                    changed = True
                    break
            if not changed:
                reason = "Stuck: Next did not advance to a new form step. Review missing fields or validation errors."
                break
            if not fields:
                if not self.navigator.ensure_form():
                    self.sync_navigation(job_meta)
                    reason = self.navigator.reason
                    break
                self.sync_navigation(job_meta)
                fields = self.observe()
                after = self.fingerprint(fields)
            if after in visited_steps:
                reason = "Stopped: navigation returned to an already visited step."
                break
            visited_steps.add(after)
            attempted.clear()
            self.log("Verified transition to a new form step.")
        else:
            reason = "Stopped at the observation limit. Review the remaining form steps."
        questions.append(reason)
        results["navigation"] = self.navigator.stats
        results["ai_usage"] = getattr(getattr(self.resolver, "llm", None), "fill_usage", {})
        self.log(reason)
        return FormFillPlan(actions=actions, questions_to_review=list(dict.fromkeys(questions)),
                            summary=reason), results
