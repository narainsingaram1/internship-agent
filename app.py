"""Streamlit Web Application for Autonomous Internship Applications."""
import os
import yaml
import json
import time
from datetime import datetime
from dotenv import load_dotenv, set_key
import streamlit as st

# Configure page
st.set_page_config(
    page_title="AutoApply AI - Internship Agent",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.yaml")
EXAMPLE_PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.example.yaml")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.yaml")
ENV_PATH = os.path.join(ROOT_DIR, ".env")
DATA_DIR = os.path.join(ROOT_DIR, "data")
APPS_TRACKER_PATH = os.path.join(DATA_DIR, "applications.json")

load_dotenv(ENV_PATH)


def load_yaml(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_yaml(path: str, data: dict):
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def get_profile():
    if os.path.exists(PROFILE_PATH):
        return load_yaml(PROFILE_PATH)
    return load_yaml(EXAMPLE_PROFILE_PATH)


def load_applications():
    if os.path.exists(APPS_TRACKER_PATH):
        try:
            with open(APPS_TRACKER_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []


# --- SIDEBAR: Status & Settings ---
st.sidebar.title("⚡ AutoApply Agent")
st.sidebar.caption("100% Free Frontier Intelligence via OpenRouter")

api_key = os.getenv("OPENROUTER_API_KEY", "")
if not api_key:
    st.sidebar.error("⚠️ OpenRouter Key missing")
    new_key = st.sidebar.text_input("Enter OpenRouter Key", type="password")
    if st.sidebar.button("Save Key"):
        if new_key:
            with open(ENV_PATH, "a") as f:
                f.write(f"\nOPENROUTER_API_KEY={new_key.strip()}\n")
            os.environ["OPENROUTER_API_KEY"] = new_key.strip()
            st.sidebar.success("Key saved!")
            st.rerun()
else:
    st.sidebar.success("🔑 OpenRouter API Key Active")

settings = load_yaml(SETTINGS_PATH)
active_model = settings.get("llm", {}).get("model", "google/gemini-2.0-flash-exp:free")
st.sidebar.info(f"🧠 **Model**: `{active_model}`\n\n💰 **Cost**: $0.00 (Free Tier)")

# Session State Initialization
if "agent_state" not in st.session_state:
    st.session_state.agent_state = "idle"  # idle, filling, review, submitted
if "session_data" not in st.session_state:
    st.session_state.session_data = {}

# --- NAVIGATION TABS ---
tab_apply, tab_profile, tab_history, tab_settings = st.tabs([
    "🎯 Quick Apply",
    "👤 Candidate Profile",
    "📊 Application Tracker",
    "⚙️ Configuration",
])

# ==========================================================
# TAB 1: QUICK APPLY & HITL VERIFICATION
# ==========================================================
with tab_apply:
    st.subheader("Apply to an Internship")
    st.markdown("Paste any job posting link (**Greenhouse**, **Lever**, **Ashby**, etc.). The engine parses the form, autofills all data, and halts for your final review before submitting.")

    col1, col2 = st.columns([4, 1])
    with col1:
        target_url = st.text_input(
            "Job Application URL",
            placeholder="https://boards.greenhouse.io/stripe/jobs/1234567 or https://jobs.lever.co/company/...",
        )
    with col2:
        headed_mode = st.checkbox("Show Browser Window", value=True, help="Leave checked to view the browser window in real-time.")

    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1:
        start_button = st.button("🚀 Start Autofill", type="primary", use_container_width=True)

    if start_button:
        if not target_url or not target_url.startswith("http"):
            st.error("Please enter a valid URL starting with http:// or https://")
        elif not os.getenv("OPENROUTER_API_KEY"):
            st.error("Please enter your OpenRouter API Key in the sidebar first!")
        else:
            st.session_state.agent_state = "running"
            st.session_state.target_url = target_url

    # RUNNING ENGINE WORKFLOW
    if st.session_state.agent_state == "running":
        progress_bar = st.progress(10)
        status_box = st.empty()

        status_box.info("🌐 Initializing stealth browser session...")
        time.sleep(0.5)

        try:
            from src.browser import BrowserSession
            from src.detector import ATSDetector
            from src.parsers import GreenhouseParser, LeverParser, GenericParser, WorkdayParser, ICIMSParser, WorkableParser
            from src.llm import LLMClient
            from src.executor import FormExecutor
            from src.controller import ApplicationController
            from src.resolver import TwoTierResolver
            from src.memory import MemoryStore

            platform, meta = ATSDetector.identify(st.session_state.target_url)
            meta["url"] = st.session_state.target_url

            progress_bar.progress(30)
            status_box.info(f"🔍 Detected Platform: **{platform.upper()}**. Launching page...")

            session = BrowserSession(headless=not headed_mode, slow_mo=30)
            page = session.navigate(st.session_state.target_url)

            # Choose parser
            if platform == "greenhouse":
                parser = GreenhouseParser()
            elif platform == "lever":
                parser = LeverParser()
            elif platform == "workday":
                parser = WorkdayParser()
            elif platform == "icims":
                parser = ICIMSParser()
            elif platform == "workable":
                parser = WorkableParser()
            else:
                parser = GenericParser()

            job_meta = parser.extract_job_metadata(page)
            job_meta.update({key: value for key, value in meta.items() if value})

            progress_bar.progress(50)
            status_box.info(f"📄 Extracting form fields for **{job_meta.get('title', 'Role')}**...")
            profile = get_profile()
            resolver = TwoTierResolver(MemoryStore(os.path.join(DATA_DIR, "memory_store.json")), LLMClient())
            executor = FormExecutor(page, profile, project_root=ROOT_DIR)
            progress_bar.progress(70)
            controller = ApplicationController(page, parser, resolver, executor)
            plan, exec_results = controller.run(profile, job_meta)
            page = controller.page
            session.page = page
            progress_bar.progress(100)
            status_box.info(plan.summary)

            # Save screenshot for UI
            screenshot_path = os.path.join(DATA_DIR, "preview.png")
            os.makedirs(DATA_DIR, exist_ok=True)
            page.screenshot(path=screenshot_path)

            st.session_state.session_data = {
                "job_meta": job_meta,
                "plan": plan.dict(),
                "exec_results": exec_results,
                "screenshot_path": screenshot_path,
                "browser_session": session,
                "page": page,
            }
            st.session_state.agent_state = "review"
            st.rerun()

        except Exception as e:
            st.session_state.agent_state = "idle"
            st.error(f"Error during autofill: {e}")
            import traceback
            st.code(traceback.format_exc())

    # REVIEW STAGE (HUMAN-IN-THE-LOOP)
    if st.session_state.agent_state == "review":
        data = st.session_state.session_data
        job_meta = data.get("job_meta", {})
        results = data.get("exec_results", {})
        plan = data.get("plan", {})

        st.divider()
        st.markdown(f"### 🛡️ Final Review: **{job_meta.get('title', 'Role')}** at **{job_meta.get('company', 'Company')}**")
        st.caption(f"Platform: {job_meta.get('platform', '').upper()} | [Open Job Link]({job_meta.get('url')})")

        # Summary Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Fields Filled", len(results.get("succeeded", [])))
        m2.metric("Failed / Skipped", len(results.get("failed", [])) + len(results.get("skipped", [])))
        m3.metric("Review Items", len(plan.get("questions_to_review", [])))

        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("#### 📋 Field Mapping Summary")
            field_table_data = []
            for item in results.get("succeeded", []):
                field_table_data.append({
                    "Field": item.get("field"),
                    "Type": item.get("type"),
                    "Filled Value": str(item.get("value"))[:60],
                    "Confidence": f"{int(item.get('confidence', 1.0) * 100)}%",
                })
            st.dataframe(field_table_data, use_container_width=True)

            if plan.get("questions_to_review"):
                st.warning("⚠️ **Review Needed on Custom Questions:**")
                for q in plan.get("questions_to_review"):
                    st.write(f"- {q}")

        with col_right:
            st.markdown("#### 📸 Form Live Screenshot")
            if os.path.exists(data.get("screenshot_path", "")):
                st.image(data.get("screenshot_path"), caption="Preview of Filled Application", use_column_width=True)

        st.divider()
        st.markdown("#### ⚡ Confirm & Submit")
        c1, c2, c3 = st.columns(3)

        with c1:
            if st.button("✅ Approve & Click Submit", type="primary", use_container_width=True):
                page = data.get("page")
                from src.cli import HITLReviewer
                from src.tracker import ApplicationTracker
                reviewer = HITLReviewer(page)
                if not reviewer.submit_application():
                    st.warning("Submission could not be confirmed. Review the open browser.")
                    st.stop()

                tracker = ApplicationTracker(APPS_TRACKER_PATH)
                tracker.record_application(
                    url=job_meta.get("url", ""),
                    company=job_meta.get("company", ""),
                    title=job_meta.get("title", ""),
                    status="Submitted",
                    fields_filled=len(results.get("succeeded", [])),
                    notes="Approved via Web App.",
                )
                data.get("browser_session").stop()
                st.session_state.agent_state = "idle"
                st.success("🎉 Application Submitted Successfully!")
                time.sleep(2)
                st.rerun()

        with c2:
            if st.button("🕹️ Keep Browser Open for Manual Edit", use_container_width=True):
                st.info("Browser window remains open! You can interact directly in Chrome. Click 'Approve & Click Submit' whenever ready.")

        with c3:
            if st.button("❌ Discard & Abort", use_container_width=True):
                data.get("browser_session").stop()
                st.session_state.agent_state = "idle"
                st.warning("Application aborted without submitting.")
                time.sleep(1)
                st.rerun()

# ==========================================================
# TAB 2: CANDIDATE PROFILE EDITOR
# ==========================================================
with tab_profile:
    st.subheader("Your Candidate Profile")
    st.markdown("All autofilled values are derived from this profile. Edit details below and click **Save Profile**.")

    profile = get_profile()
    personal = profile.get("personal", {})
    links = profile.get("links", {})
    edu = profile.get("education", {})
    work_auth = profile.get("work_authorization", {})
    eeoc = profile.get("demographics_eeoc", {})

    with st.expander("👤 Personal Information", expanded=True):
        p1, p2 = st.columns(2)
        with p1:
            first_name = st.text_input("First Name", personal.get("first_name", ""))
            last_name = st.text_input("Last Name", personal.get("last_name", ""))
            email = st.text_input("Email", personal.get("email", ""))
            phone = st.text_input("Phone", personal.get("phone", ""))
        with p2:
            location = st.text_input("Location (City, State)", personal.get("location", ""))
            linkedin = st.text_input("LinkedIn URL", links.get("linkedin", ""))
            github = st.text_input("GitHub URL", links.get("github", ""))
            portfolio = st.text_input("Portfolio URL", links.get("portfolio", ""))

    with st.expander("🎓 Education", expanded=False):
        e1, e2 = st.columns(2)
        with e1:
            school = st.text_input("School / University", edu.get("school", ""))
            degree = st.text_input("Degree", edu.get("degree", "Bachelor of Science"))
            major = st.text_input("Major", edu.get("major", "Computer Science"))
        with e2:
            gpa = st.text_input("GPA", str(edu.get("gpa", "3.8")))
            graduation_date = st.text_input("Graduation Date", edu.get("graduation_date", "May 2026"))

    with st.expander("🛂 Work Authorization", expanded=False):
        w1, w2 = st.columns(2)
        with w1:
            auth_us = st.checkbox("Authorized to work in the US?", value=work_auth.get("authorized_in_us", True))
            sponsorship_now = st.checkbox("Require sponsorship now?", value=work_auth.get("requires_sponsorship_now", False))
            sponsorship_future = st.checkbox("Require sponsorship in the future?", value=work_auth.get("requires_sponsorship_future", False))
        with w2:
            citizenship = st.selectbox(
                "Citizenship Status",
                ["U.S. Citizen", "Permanent Resident", "F-1 (CPT/OPT)", "Other"],
                index=0,
            )

    with st.expander("📄 Resume File Upload", expanded=True):
        uploaded_resume = st.file_uploader("Upload Updated Resume PDF", type=["pdf"])
        if uploaded_resume:
            resume_dest = os.path.join(ROOT_DIR, "assets", "resume.pdf")
            os.makedirs(os.path.join(ROOT_DIR, "assets"), exist_ok=True)
            with open(resume_dest, "wb") as f:
                f.write(uploaded_resume.read())
            st.success("✅ Resume saved to `assets/resume.pdf`!")

    if st.button("💾 Save Profile Changes", type="primary"):
        profile["personal"]["first_name"] = first_name
        profile["personal"]["last_name"] = last_name
        profile["personal"]["email"] = email
        profile["personal"]["phone"] = phone
        profile["personal"]["location"] = location
        profile["links"]["linkedin"] = linkedin
        profile["links"]["github"] = github
        profile["links"]["portfolio"] = portfolio
        profile["education"]["school"] = school
        profile["education"]["degree"] = degree
        profile["education"]["major"] = major
        profile["education"]["gpa"] = gpa
        profile["education"]["graduation_date"] = graduation_date
        profile["work_authorization"]["authorized_in_us"] = auth_us
        profile["work_authorization"]["requires_sponsorship_now"] = sponsorship_now
        profile["work_authorization"]["requires_sponsorship_future"] = sponsorship_future
        profile["work_authorization"]["citizenship_status"] = citizenship

        save_yaml(PROFILE_PATH, profile)
        st.success("✅ Profile successfully saved to `config/profile.yaml`!")

# ==========================================================
# TAB 3: APPLICATION TRACKER
# ==========================================================
with tab_history:
    st.subheader("Submitted Applications Tracker")
    apps = load_applications()
    if not apps:
        st.info("No applications submitted yet. Apply to your first role in the **Quick Apply** tab!")
    else:
        st.dataframe(apps, use_container_width=True)

# ==========================================================
# TAB 4: SETTINGS & MODEL CONFIG
# ==========================================================
with tab_settings:
    st.subheader("Engine & Model Configuration")
    settings = load_yaml(SETTINGS_PATH)
    llm_cfg = settings.get("llm", {})

    st.markdown("#### Free Model Selection (OpenRouter)")
    chosen_model = st.selectbox(
        "Primary Model",
        [
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "qwen/qwen-2.5-72b-instruct:free",
        ],
        index=0,
    )

    if st.button("Save Settings"):
        settings["llm"]["model"] = chosen_model
        save_yaml(SETTINGS_PATH, settings)
        st.success("Settings saved!")
