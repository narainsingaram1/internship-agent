"""Code-first navigation with compact, bounded AI decisions for unfamiliar pages."""
import hashlib
import json
import re
import time


# These actions belong to human review, never to the navigation policy.
PROTECTED = re.compile(r"submit|finish|complete application|sign.?in|log.?in|create.*account|register|agree|consent|accept.*terms|(?:with|using) (?:google|linkedin|facebook|apple)", re.I)
APPLY_NAMES = [
    "apply manually", "apply as a guest", "continue as guest", "apply without an account",
    "apply for this job", "apply for this job online", "apply now", "start application",
    "start your application", "apply",
]


class ApplicationNavigator:
    def __init__(self, page, llm=None, log=None, max_ai_calls=3, max_steps=10, timeout=5):
        self.page = page
        self.llm = llm
        self.log = log or (lambda message: None)
        self.max_ai_calls = max_ai_calls
        self.max_steps = max_steps
        self.timeout = timeout
        self.ai_calls = 0
        self.clicks = 0
        self.form_frame = None
        self.reason = ""
        self._asked = set()
        self._attempted = set()
        self._controls = {}

    def observe(self):
        """Bound observation size; retain frame scope and never send field values."""
        self._controls = {}
        snapshot = {"url": self.page.url[:240], "headings": [], "context": "", "controls": [], "fields": []}
        self.form_frame = None
        snapshot["login"] = False
        snapshot["loading"] = False
        best_form_score = 0
        for frame in self.page.frames[:8]:
            try:
                data = frame.evaluate("""() => {
                    const visible = el => Boolean(el.getClientRects().length) &&
                        getComputedStyle(el).visibility !== 'hidden';
                    const clean = (s, n) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, n);
                    const controls = Array.from(document.querySelectorAll(
                        'button, a[href], [role="button"], input[type="button"], input[type="submit"]'
                    )).filter(el => visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true');
                    const names = el => clean(el.getAttribute('aria-label') || el.innerText || el.value, 100);
                    // Prioritize application-related controls over header/footer links.
                    controls.sort((a,b) => Number(/apply|application|guest|continue|next|cookie/i.test(names(b))) -
                                             Number(/apply|application|guest|continue|next|cookie/i.test(names(a))));
                    document.querySelectorAll('[data-agent-nav]').forEach(el => el.removeAttribute('data-agent-nav'));
                    return {
                        headings: Array.from(document.querySelectorAll('h1,h2,legend,[role="dialog"] h3'))
                            .filter(visible).slice(0,5).map(el => clean(el.innerText,100)),
                        context: clean(document.body && document.body.innerText, 500),
                        login: Array.from(document.querySelectorAll('input[type="password"]')).some(visible),
                        loading: document.readyState === 'loading' || Array.from(document.querySelectorAll(
                            '[aria-busy="true"], [role="progressbar"]:not([aria-valuenow])')).some(visible),
                        controls: controls.slice(0,24).map((el,i) => {
                            el.setAttribute('data-agent-nav', String(i));
                            const cookie = el.closest('[id*="cookie"], [class*="cookie"], #onetrust-banner-sdk');
                            const final = el.type === 'submit' && Boolean(el.closest('form')) &&
                                !/^(next|continue|save and continue)$/i.test(names(el));
                            return {local_id:i,label:names(el),href:clean(el.getAttribute('href'),180),
                                    automation:clean(el.getAttribute('data-automation-id'),60),cookie:Boolean(cookie),final};
                        }),
                        fields: Array.from(document.querySelectorAll('input,select,textarea,[role="combobox"]'))
                            .filter(el => (visible(el) || el.type === 'file' ||
                                (['radio','checkbox'].includes(el.type) && el.labels && Array.from(el.labels).some(visible))) && !el.disabled &&
                                !['hidden','submit','button','search','password'].includes(el.type) &&
                                !/newsletter|subscribe|job alerts/i.test((el.closest('form') || {}).innerText || '') &&
                                !/search/i.test(el.name + ' ' + el.id + ' ' + (el.getAttribute('aria-label') || '')))
                            .slice(0,20).map(el => ({type:el.type || el.getAttribute('role') || el.tagName.toLowerCase(),
                                application:Boolean(el.closest('form') && /application|candidate|apply/i.test(
                                    el.closest('form').id + ' ' + el.closest('form').className)),
                                label:clean(el.getAttribute('aria-label') ||
                                    (el.labels && el.labels[0] && el.labels[0].innerText) || el.name || el.id,100)}))
                    };
                }""")
                snapshot["login"] |= data["login"]
                snapshot["loading"] |= data["loading"]
                snapshot["headings"].extend(data["headings"])
                if not snapshot["context"]:
                    snapshot["context"] = data["context"]
                form_score = len(data["fields"]) + 10 * sum(
                    field["type"] == "file" or bool(re.search(r"first.?name|last.?name|phone|resume", field["label"], re.I))
                    for field in data["fields"]
                )
                if data["fields"] and form_score > best_form_score:
                    self.form_frame = frame
                    best_form_score = form_score
                snapshot["fields"].extend(data["fields"])
                for control in data["controls"]:
                    if len(snapshot["controls"]) >= 32:
                        break
                    control_id = len(snapshot["controls"])
                    local_id = control.pop("local_id")
                    control["id"] = control_id
                    snapshot["controls"].append(control)
                    self._controls[control_id] = (frame, local_id, control)
            except Exception:
                continue  # A detached/loading frame will be observed on the next pass.
        snapshot["headings"] = snapshot["headings"][:6]
        snapshot["fields"] = snapshot["fields"][:20]
        return snapshot

    @staticmethod
    def fingerprint(snapshot):
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def allowed(control):
        label = control["label"]
        href = control.get("href", "")
        return bool(label) and not control.get("final") and not PROTECTED.search(label) and not re.search(
            r"^(javascript|mailto|tel):|(?:login|signin|register|submit)(?:[/?#]|$)", href, re.I
        )

    def known_action(self, snapshot):
        controls = self.available_controls(snapshot)
        # Only dismiss identified cookie UI, never a global Agree/Accept button.
        for c in controls:
            if c["cookie"] and c["label"].lower() in {"reject all", "necessary only", "close"}:
                return c["id"]
        for name in APPLY_NAMES:
            matches = [c for c in controls if c["label"].lower().strip() == name]
            if matches:
                # Duplicate desktop/mobile Apply links often lead to the same destination.
                return matches[0]["id"]
        for c in controls:
            if c["automation"] in {"adventureButton", "applyManually", "applyWithoutResume"}:
                return c["id"]
        return None

    def available_controls(self, snapshot):
        fingerprint = self.fingerprint(snapshot)
        return [c for c in snapshot["controls"] if self.allowed(c) and
                (fingerprint, c["label"], c["href"]) not in self._attempted]

    def choose(self, snapshot, goal="reach application form"):
        control_id = self.known_action(snapshot)
        if control_id is not None:
            return "click", control_id
        fingerprint = self.fingerprint(snapshot)
        if not self.available_controls(snapshot):
            self.reason = self.reason or "No usable navigation controls found. Review the live browser."
            return "handoff", None
        if fingerprint in self._asked or self.ai_calls >= self.max_ai_calls:
            self.reason = "Navigation paused: unchanged page or AI decision budget exhausted."
            return "handoff", None
        if self.llm is None or not hasattr(self.llm, "choose_navigation"):
            self.reason = "Unfamiliar page: review the live browser."
            return "handoff", None
        self._asked.add(fingerprint)
        self.ai_calls += 1
        self.log(f"Unfamiliar page: compact AI navigation decision {self.ai_calls}/{self.max_ai_calls}.")
        compact = {
            "url": snapshot["url"][:200], "headings": snapshot["headings"][:4],
            "context": snapshot["context"][:400], "fields": snapshot["fields"][:12],
            "controls": [{"id": c["id"], "label": c["label"][:80], "href": c["href"][:80]}
                         for c in self.available_controls(snapshot)][:20],
        }
        while len(json.dumps(compact, ensure_ascii=False)) > 5500 and compact["controls"]:
            compact["controls"].pop()
        decision = self.llm.choose_navigation(compact, goal)
        self.reason = decision.reason
        if decision.action == "click":
            observed = self._controls.get(decision.control_id)
            sent_ids = {c["id"] for c in compact["controls"]}
            if decision.control_id not in sent_ids or not observed or not self.allowed(observed[2]):
                self.reason = "AI proposed an unavailable or protected control; paused for review."
                return "handoff", None
        return decision.action, decision.control_id

    def click(self, control_id, snapshot):
        frame, local_id, control = self._controls[control_id]
        attempt = (self.fingerprint(snapshot), control["label"], control["href"])
        if attempt in self._attempted:
            self.reason = "Navigation stalled: this action already failed on this page."
            return False
        self._attempted.add(attempt)
        locator = frame.locator(f'[data-agent-nav="{local_id}"]')
        pages_before = set(self.page.context.pages)
        context = self.page.context
        before = self.fingerprint(snapshot)
        self.log(f"Navigation: {control['label']}")
        try:
            current_label = locator.evaluate("el => (el.getAttribute('aria-label') || el.innerText || el.value || '').replace(/\\s+/g, ' ').trim().slice(0,100)")
            if current_label != control["label"]:
                self.reason = "Navigation control changed while AI was deciding. Review the live page."
                return False
            locator.click(timeout=3000)
            self.clicks += 1
        except Exception:
            self.reason = "Navigation control could not be clicked. Review overlays or the live page."
            return False
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            new_pages = [p for p in context.pages if p not in pages_before and not p.is_closed()]
            if new_pages:
                self.page = new_pages[-1]
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    self.reason = "The new application tab is still loading. Resume when it is ready."
                    return False
            if self.page.is_closed():
                self.reason = "The application tab was closed. Open the application again."
                return False
            self.page.wait_for_timeout(150)
            after = self.observe()
            if not after["loading"] and (after["controls"] or after["fields"] or after["login"]) and self.fingerprint(after) != before:
                return True
        self.reason = "Navigation stalled: clicking the control did not change the page."
        return False

    def ensure_form(self):
        for _ in range(self.max_steps):
            snapshot = self.observe()
            if snapshot["loading"]:
                self.page.wait_for_timeout(500)
                continue
            if re.search(r"verify (?:you are|you're) human|security verification|complete.*captcha|i.m not a robot",
                         " ".join(snapshot["headings"]) + " " + snapshot["context"], re.I):
                self.reason = "Human verification required. Complete it in the live browser, then resume."
                return False
            if snapshot["login"] or (self.known_action(snapshot) is None and re.search(
                r"sign in|log in|create an account", " ".join(snapshot["headings"]), re.I
            )):
                self.reason = "Login required. Sign in in the live browser, then resume."
                return False
            apply_id = self.known_action(snapshot)
            if not snapshot["fields"] and any(
                c.get("final") or re.search(r"submit|finish|complete application", c["label"], re.I)
                for c in snapshot["controls"]
            ):
                self.reason = "Application is at review. Check the final details before submitting."
                return False
            # Search/newsletter controls are not a reason to skip the Apply landing flow.
            confident_form = any(f.get("application") or f["type"] == "file" or re.search(
                r"first.?name|last.?name|phone|school|resume", f["label"], re.I
            ) for f in snapshot["fields"])
            if snapshot["fields"] and (apply_id is None or confident_form):
                self.reason = "Application form reached."
                return True
            action, control_id = self.choose(snapshot)
            if action == "handoff":
                return False
            if action == "wait":
                self.page.wait_for_timeout(500)
                continue
            if not self.click(control_id, snapshot):
                # A failed known button may be blocked by an unfamiliar overlay.
                # Re-observe and permit a different action, never repeat the failed click.
                continue
        self.reason = "Navigation stopped at the transition limit. Review the live page."
        return False

    @property
    def stats(self):
        return {"ai_calls": self.ai_calls, "clicks": self.clicks,
                **{k: v for k, v in getattr(self.llm, "navigation_usage", {}).items() if k != "calls"}}
