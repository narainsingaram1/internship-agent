# 🚀 Autonomous Internship Application Agent

A high-precision, hybrid automated internship application engine powered by **100% free frontier models via OpenRouter** (e.g., Gemini 2.0 Flash / Llama 3.3 70B) and Playwright.

---

## ✨ Key Features

1. **$0.00 Running Cost**: Uses top-tier free endpoints on OpenRouter (`google/gemini-2.0-flash-exp:free`, `meta-llama/llama-3.3-70b-instruct:free`).
2. **ATS Fast-Paths**: Instant, deterministic DOM parsing for **Greenhouse**, **Lever**, and generic career pages.
3. **Smart Question Answering**: Synthesizes concise, authentic responses for short-answer questions (*"Why do you want to work here?"*) grounded strictly in your experience.
4. **Mandatory Human Verification (HITL)**: Never submits on its own. Displays a clean terminal diff, opens the live browser for review, and only submits when you confirm with `[S]` or `[T]`.
5. **Anti-Bot Stealth**: Human-like keystroke delays, disabled automation flags, and clean viewport simulation.

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

### Option 1: Modern Interactive Web App (Recommended 🚀)
Launch the full web dashboard in your browser:
```bash
./venv/bin/streamlit run app.py
```
* **Interactive Dashboard**: Zero HTML coding needed, reactive real-time dashboard.
* **Paste & Go**: Enter any job link, toggle live browser visibility.
* **Review Screen**: View live screenshots of filled forms, field tables, and generated short essays.
* **One-Click Actions**: Approve & Submit, keep browser open for manual tweaking, or abort.
* **Visual Profile Editor**: Update your candidate details, work authorization, and drag-and-drop your resume PDF without touching YAML.

---

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
