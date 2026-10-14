# 🚀 Autonomous Internship Application Agent

A hybrid internship application assistant using Playwright for browser actions and OpenRouter for bounded navigation decisions and unresolved questions.

---

## ✨ Key Features

1. **Bounded AI usage**: Known Apply flows and profile fields use code. Unfamiliar navigation allows at most 3 AI requests per session; answer generation allows at most 4 requests, including fallback attempts. Provider pricing and availability depend on the configured model.
2. **ATS Fast-Paths**: Instant, deterministic DOM parsing for **Greenhouse**, **Lever**, and generic career pages.
3. **Smart Question Answering**: Synthesizes concise, authentic responses for short-answer questions (*"Why do you want to work here?"*) grounded strictly in your experience.
4. **Mandatory Human Verification (HITL)**: Never submits on its own. Displays a clean terminal diff, opens the live browser for review, and only submits when you confirm with `[S]` or `[T]`.
5. **Navigation and recovery**: Follow Apply/Apply Manually transitions, redirects, new tabs, and embedded forms. Verify page changes, discover conditional fields, and pause on login or human verification.

---

## 🛠️ Quick Setup (2 Minutes)

### 1. Add your OpenRouter API Key
Create a `.env` file in the project root:
```bash
cp .env.example .env
```
Open `.env` and add your key:
```env
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxx
```
*(Get a free key at [openrouter.ai/keys](https://openrouter.ai/keys))*

### 2. Configure Your Profile
Copy the example profile and personalize it:
```bash
cp config/profile.example.yaml config/profile.yaml
```
Open `config/profile.yaml` to update:
- Your name, email, phone, and university details.
- Work authorization (U.S. Citizen / sponsorship preferences).
- EEOC demographic preferences (defaults to "Decline to Self Identify").
- Place your PDF resume in `assets/resume.pdf`.

---

## 🎯 How to Use

### Option 1: Custom Minimalist Web UI (Recommended 🚀)
Launch the dark-mode web application in your browser:
```bash
./venv/bin/python serve.py
```
* **Vercel / Linear Minimalist Aesthetic**: Deep zinc dark mode, Inter & JetBrains Mono typography, zero clunky widgets.
* **Omnibar**: Paste any job link and toggle "Watch Live Browser" mode.
* **Real-time Pipeline & Logs**: Live WebSocket log stream and 5-stage progress indicator.
* **HITL Verification Panel**: Live high-res application screenshot, field mapping table with confidence badges, and one-click actions:
  - 🟢 **Approve & Submit**
  - 🕹️ **Manual Live Browser Edit**
  - ✕ **Abort & Discard**
* **Slide-over Profile & History Drawers**: Edit personal info and drag-and-drop resume PDFs directly in the browser.

---


### Navigation, efficiency, and resume

Start with the job posting URL, not an already-open form. The shared controller first navigates toward the form, then fills and verifies fields. Known controls use no navigation AI requests. Unfamiliar pages send a compact snapshot (at most 5,500 characters of page data), with no candidate profile, raw HTML, screenshot, or conversation history. Navigation replies are capped at 160 output tokens and may select only a currently observed control.

Use **Watch Live Browser** when authentication may be required. After signing in, clearing a challenge, or editing a field, choose **Resume autofill** in the dashboard. The browser stays on its owning worker thread; the same navigation and answer-call budgets carry across resumes. Previously verified fields are preserved so manual corrections are not overwritten. Final submission requires approval and a visible receipt before a Submitted record is written.

Generated answers remain drafts. The cache reuses only exact questions marked as approved global facts; company-specific essays and old unapproved generated answers are not automatically reused. Missing facts and ambiguous options remain for review rather than being guessed.

The controller is bounded, not a guarantee of completion across every employer site. Unsupported widgets and unavailable models can still require manual completion. Logs include navigation calls, clicks, and provider-reported token counts when available.

Run the local regression suite (browser pages and provider responses are fixtures; no applications are sent):
```bash
./venv/bin/python -m unittest discover -s tests -v
```

### Option 2: Terminal CLI
Run the agent directly from terminal:
```bash
./venv/bin/python run.py "https://boards.greenhouse.io/company/jobs/1234567"
```

### What Happens:
1. The browser launches in headed mode so you can see the application being filled live.
2. The agent identifies the ATS (Greenhouse, Lever, etc.) and extracts all interactive fields.
3. The OpenRouter LLM matches fields against your `profile.yaml`.
4. Playwright fills inputs, selects dropdowns, and attaches your `assets/resume.pdf`.
5. **The Verification Barrier activates**:
   - The terminal shows a colorized table of every field filled.
   - Any essay questions or low-confidence fields are highlighted for your review.
   - Choose:
     - `[S]`: Approve and let the agent click final submit.
     - `[T]`: Take over control in the live browser (edit anything, solve CAPTCHAs, then submit).
     - `[A]`: Abort immediately without submitting.
6. The application is recorded in `data/applications.json`.
