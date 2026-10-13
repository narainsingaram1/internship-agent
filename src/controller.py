"""Bounded observe/fill/verify loop shared by the web and terminal entrypoints."""
import json
import re
import time

from .llm.prompts import FormFillPlan
from .parsers import GenericParser


class ApplicationController:
    def __init__(self, page, parser, resolver, executor, log=None, max_passes=20, transition_timeout=5):
        self.page = page
        self.parser = parser
        self.resolver = resolver
        self.executor = executor
        self.log = log or (lambda message: None)
        self.max_passes = max_passes
        self.transition_timeout = transition_timeout

    @staticmethod
    def field_key(field):
        return json.dumps({k: field.get(k) for k in (
            "selector", "label", "name", "type", "options"
        )}, sort_keys=True)

    def observe(self, prepare=False):
        platform = self.parser.platform_name
        if platform in {"generic", "workday", "workable", "icims"}:
            fields = self.parser.extract_fields(self.page, prepare=prepare)
        else:
            fields = self.parser.extract_fields(self.page)
        if not fields:
            fields = GenericParser().extract_fields(self.page, prepare=prepare)
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
        headings = self.page.locator("h1:visible, h2:visible, legend:visible, [aria-current='step']:visible").all_inner_texts()
        return (self.page.url, tuple(headings), tuple(sorted(self.field_key(f) for f in fields)))

    def next_button(self):
        # Never infer final submission from a generic type=submit or Apply button.
        buttons = self.page.get_by_role("button", name=re.compile(
            r"^(next|save and continue|continue)$", re.I
        ))
        candidates = [buttons.nth(i) for i in range(buttons.count())
                      if buttons.nth(i).is_visible() and buttons.nth(i).is_enabled()]
        return candidates[0] if len(candidates) == 1 else None

    def validation_errors(self):
        return self.page.locator(
            "[aria-invalid='true']:visible, [role='alert']:visible"
        ).count() > 0

    def run(self, profile, job_meta):
        results = {"succeeded": [], "failed": [], "skipped": []}
        actions, questions = [], []
        attempted = set()
        fields = self.observe(prepare=True)
        visited_steps = {self.fingerprint(fields)}
        reason = "Review the completed fields before submission."
        for pass_index in range(self.max_passes):
            # Authentication is a handoff, never another form-mapping request.
            if self.page.locator("input[type='password']:visible").count():
                reason = "Login required. Sign in in the live browser before continuing."
                break
            if not fields:
                reason = "No editable application fields found. Review the live page."
                break
            pending = [f for f in fields if self.field_key(f) not in attempted]
            if pending:
                self.log(f"Pass {pass_index + 1}: resolving {len(pending)} new or changed fields.")
                attempted.update(self.field_key(f) for f in pending)
                plan = self.resolver.resolve_form(pending, profile, job_meta)
                actions.extend(plan.actions)
                questions.extend(plan.questions_to_review)
                batch = self.executor.execute_plan(plan)
                for key in results:
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
                break
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
                if fields and after != before:
                    changed = True
                    break
            if not changed:
                reason = "Stuck: Next did not advance to a new form step. Review missing fields or validation errors."
                break
            if after in visited_steps:
                reason = "Stopped: navigation returned to an already visited step."
                break
            visited_steps.add(after)
            attempted.clear()
            self.log("Verified transition to a new form step.")
        else:
            reason = "Stopped at the observation limit. Review the remaining form steps."
        questions.append(reason)
        self.log(reason)
        return FormFillPlan(actions=actions, questions_to_review=list(dict.fromkeys(questions)),
                            summary=reason), results
