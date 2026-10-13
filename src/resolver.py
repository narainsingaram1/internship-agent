"""Two-Tier Form Resolution Engine: Deterministic Fast-Path + AI Question Brain."""
import re
import logging
from typing import Dict, Any, List, Tuple
from .memory import MemoryStore
from .llm.client import LLMClient
from .llm.prompts import FormFillPlan, FormFieldAction

logger = logging.getLogger(__name__)


class TwoTierResolver:
    """Combines instant deterministic matching for standard fields with AI reasoning and memory for screening questions."""

    def __init__(self, memory_store: MemoryStore = None, llm_client: LLMClient = None):
        self.memory = memory_store or MemoryStore()
        self.llm = llm_client or LLMClient()

    def resolve_form(
        self,
        form_elements: List[Dict[str, Any]],
        candidate_profile: Dict[str, Any],
        job_metadata: Dict[str, Any] = None,
    ) -> FormFillPlan:
        """Resolve all fields: Tier 1 (Deterministic) handles standard fields; Tier 2 (AI + Memory) handles custom questions."""
        job_metadata = job_metadata or {}
        resolved_actions: List[FormFieldAction] = []
        unmatched_fields: List[Dict[str, Any]] = []
        questions_to_review: List[str] = []

        # -------------------------------------------------------------------------
        # TIER 1: DETERMINISTIC FAST-PATH (Instant, 100% Reliable, 0 Token Cost)
        # -------------------------------------------------------------------------
        for el in form_elements:
            action = self._match_deterministic(el, candidate_profile)
            if action:
                resolved_actions.append(action)
            else:
                unmatched_fields.append(el)

        logger.info(
            f"Tier 1 matched {len(resolved_actions)} standard fields deterministically. "
            f"{len(unmatched_fields)} fields sent to AI / Memory Brain."
        )

        # -------------------------------------------------------------------------
        # TIER 2: CONTINUOUS MEMORY + AI QUESTION BRAIN (For Screening & Essay Questions)
        # -------------------------------------------------------------------------
        ai_pending_fields: List[Dict[str, Any]] = []

        for el in unmatched_fields:
            label = el.get("label", "")
            options = [o.get("text", "") for o in el.get("options", []) if isinstance(o, dict)]

            # Check Continuous Memory first
            remembered_answer = self.memory.get_answer(label, options)
            if remembered_answer:
                logger.info(f"Memory hit for '{label[:40]}...': {remembered_answer[:30]}")
                action_type = "select_option" if el.get("tag") == "select" or el.get("options") else "type_text"
                if el.get("type") in ["checkbox", "radio"]:
                    action_type = "click_checkbox"

                resolved_actions.append(
                    FormFieldAction(
                        selector=el.get("selector", ""),
                        field_name=label or el.get("name", ""),
                        action_type=action_type,
                        value=remembered_answer,
                        confidence=0.98,
                        reasoning="Recalled from Continuous Memory Store",
                    )
                )
            else:
                ai_pending_fields.append(el)

        # For remaining custom questions, call AI via OpenRouter
        if ai_pending_fields:
            logger.info(f"Invoking AI Brain via OpenRouter for {len(ai_pending_fields)} custom questions...")
            ai_plan = self.llm.generate_fill_plan(ai_pending_fields, candidate_profile, job_metadata)

            for act in ai_plan.actions:
                resolved_actions.append(act)
                # Auto-save AI-generated answers to Continuous Memory for future applications
                if act.action_type in ["type_text", "select_option"] and act.value and len(act.value) > 1:
                    self.memory.save_answer(act.field_name, act.value, source="ai_generated")

            questions_to_review.extend(ai_plan.questions_to_review)

        summary_msg = f"Resolved {len(resolved_actions)} fields ({len(resolved_actions) - len(ai_pending_fields)} deterministic/memory, {len(ai_pending_fields)} AI reasoned)."
        return FormFillPlan(
            actions=resolved_actions,
            questions_to_review=list(set(questions_to_review)),
            summary=summary_msg,
        )

    def _match_deterministic(
        self, el: Dict[str, Any], profile: Dict[str, Any]
    ) -> FormFieldAction | None:
        """Pattern match standard profile attributes to form fields."""
        personal = profile.get("personal", {})
        links = profile.get("links", {})
        edu = profile.get("education", {})
        work = profile.get("work_authorization", {})
        eeoc = profile.get("demographics_eeoc", {})
        prefs = profile.get("preferences", {})

        name = (el.get("name") or "").lower()
        id_str = (el.get("id") or "").lower()
        data_ui = (el.get("data_ui") or "").lower()
        label = (el.get("label") or "").lower()
        placeholder = (el.get("placeholder") or "").lower()
        el_type = (el.get("type") or "").lower()
        tag = (el.get("tag") or "").lower()
        selector = el.get("selector", "")

        blob = f"{name} {id_str} {data_ui} {label} {placeholder}"

        # 1. First Name
        if any(k in blob for k in ["first_name", "firstname", "first name", "fname", "first"]) and not any(k in blob for k in ["last", "full", "confirm"]):
            val = personal.get("first_name", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="First Name", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched First Name"
                )

        # 2. Last Name
        if any(k in blob for k in ["last_name", "lastname", "last name", "lname", "surname", "last"]) and "first" not in blob:
            val = personal.get("last_name", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Last Name", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Last Name"
                )

        # 3. Full Name (if single input)
        if (name == "name" or label == "full name" or label == "name") and not any(k in blob for k in ["first", "last", "company"]):
            full_name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
            if full_name:
                return FormFieldAction(
                    selector=selector, field_name="Full Name", action_type="type_text",
                    value=full_name, confidence=1.0, reasoning="Matched Full Name"
                )

        # 4. Email
        if any(k in blob for k in ["email", "e-mail"]) and "confirm" not in blob:
            val = personal.get("email", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Email", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Email"
                )

        # 5. Phone
        if any(k in blob for k in ["phone", "telephone", "mobile", "tel", "cell"]):
            val = str(personal.get("phone", ""))
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Phone Number", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Phone"
                )

        # 6. Street Address
        if any(k in blob for k in ["street", "address1", "address_1", "address_line_1", "line 1"]) or (
            blob.strip() in ["address", "candidate address", "your address"] or name == "address" or id_str == "address"
        ):
            val = personal.get("address", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Address", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Address"
                )

        # 7. City
        if any(k in blob for k in ["city", "town"]) and not any(k in blob for k in ["state", "country", "zip"]):
            val = personal.get("city", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="City", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched City"
                )

        # 8. County
        if "county" in blob or "district" in blob:
            val = personal.get("county", personal.get("state", ""))
            if val:
                return FormFieldAction(
                    selector=selector, field_name="County", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched County"
                )

        # 9. State / Province
        if any(k in blob for k in ["state", "province", "region"]) and "united" not in blob:
            val = personal.get("state", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="State", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched State"
                )

        # 10. Country
        if any(k in blob for k in ["country", "nation"]):
            val = personal.get("country", "United States")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Country", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Country"
                )

        # 11. Postal Code / Zip
        if any(k in blob for k in ["zip", "postal", "postcode", "post code", "zipcode"]):
            val = personal.get("zip_code", personal.get("postal_code", ""))
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Postal Code", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Postal Code"
                )

        # 12. Resume File Upload
        if el_type == "file" or any(k in blob for k in ["resume", "cv", "curriculum vitae"]):
            return FormFieldAction(
                selector=selector, field_name="Resume File Upload", action_type="upload_file",
                value="resume", confidence=1.0, reasoning="Matched Resume Attachment"
            )

        # 13. LinkedIn
        if "linkedin" in blob:
            val = links.get("linkedin", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="LinkedIn Profile", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched LinkedIn"
                )

        # 14. GitHub
        if "github" in blob:
            val = links.get("github", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="GitHub Profile", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched GitHub"
                )

        # 15. Portfolio / Website
        if any(k in blob for k in ["portfolio", "website", "personal site", "blog"]):
            val = links.get("portfolio", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Portfolio Link", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Portfolio"
                )

        # 16. School / University
        if any(k in blob for k in ["school", "university", "college", "institution"]):
            val = edu.get("school", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="University", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched University"
                )

        # 17. Degree
        if any(k in blob for k in ["degree", "qualification"]):
            val = edu.get("degree", "Bachelor of Science")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Degree", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Degree"
                )

        # 18. Major
        if any(k in blob for k in ["major", "field of study", "discipline"]):
            val = edu.get("major", "Computer Science")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Major", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Major"
                )

        # 19. GPA
        if "gpa" in blob:
            val = str(edu.get("gpa", "3.8"))
            if val:
                return FormFieldAction(
                    selector=selector, field_name="GPA", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched GPA"
                )

        # 20. Graduation Date
        if any(k in blob for k in ["grad date", "graduation date", "expected grad", "graduation year"]):
            val = edu.get("graduation_date", "May 2026")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Graduation Date", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Graduation Date"
                )

        # 21. Veteran Status (EEOC)
        if any(k in blob for k in ["veteran", "military"]):
            vet_val = eeoc.get("veteran_status", "I am not a protected veteran")
            return FormFieldAction(
                selector=selector, field_name="Veteran Status", action_type="select_option" if tag == "select" or el.get("options") else "type_text",
                value=vet_val, confidence=1.0, reasoning="Matched Veteran Status (Not a protected veteran)"
            )

        # 22. Disability Status (EEOC)
        if "disability" in blob:
            dis_val = eeoc.get("disability_status", "No, I do not have a disability")
            return FormFieldAction(
                selector=selector, field_name="Disability Status", action_type="select_option" if tag == "select" or el.get("options") else "type_text",
                value=dis_val, confidence=1.0, reasoning="Matched Disability Status (No disability)"
            )

        # 23. Gender (EEOC)
        if any(k in blob for k in ["gender", "sex"]) and "sexual" not in blob:
            g_val = eeoc.get("gender", "Decline To Self Identify")
            return FormFieldAction(
                selector=selector, field_name="Gender", action_type="select_option" if tag == "select" or el.get("options") else "type_text",
                value=g_val, confidence=1.0, reasoning="Matched Gender Preference"
            )

        # 24. Race / Ethnicity (EEOC)
        if any(k in blob for k in ["race", "ethnicity", "ethnic"]):
            r_val = eeoc.get("race_ethnicity", "Decline To Self Identify")
            return FormFieldAction(
                selector=selector, field_name="Race/Ethnicity", action_type="select_option" if tag == "select" or el.get("options") else "type_text",
                value=r_val, confidence=1.0, reasoning="Matched Race Preference"
            )

        # 25. GDPR / Terms Agreement
        if any(k in blob for k in ["gdpr", "consent", "terms", "agree", "privacy", "data processing"]):
            if el_type in ["checkbox", "radio"]:
                return FormFieldAction(
                    selector=selector, field_name="Consent Agreement", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Agreed to privacy/application terms"
                )

        return None
