"""Two-Tier Form Resolution Engine: Deterministic Fast-Path + AI Question Brain."""
import re
import logging
from typing import Dict, Any, List, Optional
from .memory import MemoryStore
from .llm.client import LLMClient
from .llm.prompts import FormFillPlan, FormFieldAction

logger = logging.getLogger(__name__)


class TwoTierResolver:
    """Combines instant deterministic matching for standard fields with AI reasoning and continuous memory for screening questions."""

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
        kb = profile.get("screening_knowledge_base", {})

        name = (el.get("name") or "").lower()
        id_str = (el.get("id") or "").lower()
        data_ui = (el.get("data_ui") or "").lower()
        label = (el.get("label") or "").lower()
        placeholder = (el.get("placeholder") or "").lower()
        el_type = (el.get("type") or "").lower()
        tag = (el.get("tag") or "").lower()
        selector = el.get("selector", "")
        group_title = (el.get("group_title") or "").lower()
        option_text = (el.get("option_text") or "").lower()

        blob = f"{name} {id_str} {data_ui} {label} {placeholder}".strip()

        # GUARD: Textareas asking long questions or essays should NOT be hijacked by simple keyword matches
        is_long_prompt = tag == "textarea" or len(label) > 60 or any(k in label for k in ["300 words", "why are you interested", "trading experience", "accomplishments", "responsibilities"])

        # 1. First Name
        if not is_long_prompt and any(k in blob for k in ["first_name", "firstname", "first name", "fname"]) and not any(k in blob for k in ["last", "full", "confirm"]):
            val = personal.get("first_name", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="First Name", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched First Name"
                )

        # 2. Last Name
        if not is_long_prompt and any(k in blob for k in ["last_name", "lastname", "last name", "lname", "surname"]) and "first" not in blob:
            val = personal.get("last_name", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Last Name", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Last Name"
                )

        # 3. Full Name (if single input)
        if not is_long_prompt and (name == "name" or label == "full name" or label == "name") and not any(k in blob for k in ["first", "last", "company", "school", "course", "degree"]):
            full_name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
            if full_name:
                return FormFieldAction(
                    selector=selector, field_name="Full Name", action_type="type_text",
                    value=full_name, confidence=1.0, reasoning="Matched Full Name"
                )

        # 4. Email
        if not is_long_prompt and any(k in blob for k in ["email", "e-mail"]) and "confirm" not in blob:
            val = personal.get("email", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Email", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Email"
                )

        # 5. Phone
        if not is_long_prompt and any(k in blob for k in ["phone", "telephone", "mobile", "tel", "cell"]):
            val = str(personal.get("phone", ""))
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Phone Number", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Phone"
                )

        # 6. Street Address
        if not is_long_prompt and (any(k in blob for k in ["street", "address1", "address_1", "address_line_1", "line 1"]) or (
            blob.strip() in ["address", "candidate address", "your address"] or name == "address" or id_str == "address" or label == "address"
        )):
            val = personal.get("address", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Address", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Address"
                )

        # 7. City
        if not is_long_prompt and (label == "city" or name == "city" or id_str == "city" or any(k in blob for k in ["city", "town"])) and not any(k in blob for k in ["state", "country", "zip"]):
            val = personal.get("city", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="City", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched City"
                )

        # 8. County
        if not is_long_prompt and ("county" in blob or "district" in blob):
            val = personal.get("county", personal.get("state", ""))
            if val:
                return FormFieldAction(
                    selector=selector, field_name="County", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched County"
                )

        # 9. State / Province (Strict match, never collide with essay questions containing 'state why')
        if not is_long_prompt and (label in ["state", "state/province", "state / province", "province", "region"] or name in ["state", "province", "region"] or id_str in ["state", "province"]):
            val = personal.get("state", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="State", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched State"
                )

        # 10. Country (Strict match, never collide with 'nationality and visas')
        if not is_long_prompt and (label in ["country", "country of residence", "residence country"] or name in ["country", "country_id"] or id_str == "country") and "nationality" not in blob:
            val = personal.get("country", "United States")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Country", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Country"
                )

        # 11. Postal Code / Zip
        if not is_long_prompt and any(k in blob for k in ["zip", "postal", "postcode", "post code", "zipcode"]):
            val = personal.get("zip_code", personal.get("postal_code", ""))
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Postal Code", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Postal Code"
                )

        # 12. Resume File Upload
        if el_type == "file" or (
            any(k in blob for k in ["resume", "curriculum vitae", "attach cv", "upload cv"])
            and el_type not in ["radio", "checkbox"]
        ):
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

        # 15. Portfolio / Website (Strict match, never collide with trading portfolio)
        if not is_long_prompt and any(k in blob for k in ["portfolio link", "portfolio url", "personal website", "portfolio website", "personal site", "github/portfolio"]) and "trading" not in blob:
            val = links.get("portfolio", "")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Portfolio Link", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Matched Portfolio"
                )

        # 16. School / University
        # A) Combobox: 'What school are you currently attending?' -> Select candidate university
        if any(k in blob for k in ["what school are you currently attending", "select your university", "select your school"]):
            val = edu.get("school", "Georgia Institute of Technology")
            return FormFieldAction(
                selector=selector, field_name="School Dropdown", action_type="select_option",
                value=val, confidence=1.0, reasoning=f"Select Candidate University: {val}"
            )
        # B) Dependent fallback text input: 'enter your school below' or 'if you have selected other'
        if any(k in blob for k in ["enter your school below", "if you have selected 'other'", "if other"]):
            val = edu.get("school", "Georgia Institute of Technology")
            return FormFieldAction(
                selector=selector, field_name="University Name (If Other)", action_type="type_text",
                value=val, confidence=0.9, reasoning="University fallback if 'Other' was selected"
            )

        # 17. Degree Level (Radio or Text)
        if el_type == "radio" and any(k in blob for k in ["level of degree", "degree level", "degree are you currently completing"]):
            user_deg = edu.get("degree", "Bachelor of Science").lower()
            if "bachelor" in label and "bachelor" in user_deg:
                return FormFieldAction(
                    selector=selector, field_name="Degree (Bachelors)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Matched Degree: Bachelors"
                )
            if "master" in label and "master" in user_deg:
                return FormFieldAction(
                    selector=selector, field_name="Degree (Masters)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Matched Degree: Masters"
                )
            if "phd" in label and "phd" in user_deg:
                return FormFieldAction(
                    selector=selector, field_name="Degree (PhD)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Matched Degree: PhD"
                )
            return FormFieldAction(
                selector=selector, field_name=label, action_type="skip",
                value="", confidence=1.0, reasoning="Not candidate degree level"
            )

        # 18. Broad Degree / Subject
        if any(k in blob for k in ["broad’ degree", "broad degree", "degree/ subject", "broad subject"]):
            return FormFieldAction(
                selector=selector, field_name="Broad Degree Subject", action_type="select_option",
                value="Computer Science", confidence=1.0, reasoning="Selected Broad Degree Subject: Computer Science"
            )

        # 19. Major / Degree Course Specific Name
        if any(k in blob for k in ["specific name of your degree course", "degree course/ major", "degree course", "specific major"]):
            val = edu.get("major", "Computer Science")
            if val:
                return FormFieldAction(
                    selector=selector, field_name="Degree Course / Major", action_type="type_text",
                    value=val, confidence=1.0, reasoning="Entered Major: Computer Science"
                )

        # 20. GPA / Expected Grade
        if any(k in blob for k in ["gpa", "expected grade/gpa", "expected grade", "provide the scale"]):
            val = str(edu.get("gpa", "3.89"))
            scale = str(edu.get("gpa_scale", "4.0"))
            return FormFieldAction(
                selector=selector, field_name="GPA / Grade", action_type="type_text",
                value=f"{val} / {scale}", confidence=1.0, reasoning="Entered GPA Scale"
            )

        # 21. Graduation Year / Date
        if any(k in blob for k in ["graduation year", "expected graduation year", "year of graduation"]):
            grad_year = "2026"
            if edu.get("graduation_date"):
                match = re.search(r"\b(202\d)\b", edu["graduation_date"])
                if match:
                    grad_year = match.group(1)
            return FormFieldAction(
                selector=selector, field_name="Graduation Year", action_type="select_option" if tag == "select" or "input" in selector else "type_text",
                value=grad_year, confidence=1.0, reasoning=f"Selected Graduation Year: {grad_year}"
            )

        # 22. Office Location Top Choice (Radio)
        if el_type == "radio" and "top choice" in blob:
            pref_loc = prefs.get("preferred_location", "New York").lower()
            is_ny = "new york" in label and ("new york" in pref_loc or "sf" in pref_loc or "san francisco" in pref_loc or "us" in pref_loc)
            is_london = "london" in label and "london" in pref_loc
            if is_ny or (not any(c in pref_loc for c in ["london", "singapore", "hong kong"]) and "new york" in label):
                return FormFieldAction(
                    selector=selector, field_name="Top Choice Location (New York)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Selected Top Choice: New York"
                )
            if is_london:
                return FormFieldAction(
                    selector=selector, field_name="Top Choice Location (London)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Selected Top Choice: London"
                )
            return FormFieldAction(
                selector=selector, field_name=label, action_type="skip",
                value="", confidence=1.0, reasoning="Not primary office choice"
            )

        # 23. Other Office Locations Open To (Checkbox)
        if el_type == "checkbox" and any(k in blob for k in ["other office locations", "would be open to"]):
            if "london" in label:
                return FormFieldAction(
                    selector=selector, field_name="Open Location (London)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Open to London location"
                )
            if "singapore" in label:
                return FormFieldAction(
                    selector=selector, field_name="Open Location (Singapore)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Open to Singapore location"
                )
            if "hong kong" in label:
                return FormFieldAction(
                    selector=selector, field_name="Open Location (Hong Kong)", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Open to Hong Kong location"
                )
            return FormFieldAction(
                selector=selector, field_name=label, action_type="skip",
                value="", confidence=1.0, reasoning="Skipped other locations"
            )

        # 24. Gender (EEOC & Radios)
        if any(k in blob for k in ["specify your gender", "gender", "sex"]) and "sexual" not in blob:
            user_gender = eeoc.get("gender", "Male").lower()
            if el_type == "radio":
                if "male" in label and "female" not in label and "male" in user_gender and "female" not in user_gender:
                    return FormFieldAction(
                        selector=selector, field_name="Gender (Male)", action_type="click_checkbox",
                        value="true", confidence=1.0, reasoning="Matched Gender: Male"
                    )
                if "female" in label and "female" in user_gender:
                    return FormFieldAction(
                        selector=selector, field_name="Gender (Female)", action_type="click_checkbox",
                        value="true", confidence=1.0, reasoning="Matched Gender: Female"
                    )
                if any(k in label for k in ["prefer not to say", "decline"]) and any(k in user_gender for k in ["decline", "prefer not"]):
                    return FormFieldAction(
                        selector=selector, field_name="Gender (Prefer not to say)", action_type="click_checkbox",
                        value="true", confidence=1.0, reasoning="Matched Gender: Prefer not to say"
                    )
                return FormFieldAction(
                    selector=selector, field_name=label, action_type="skip",
                    value="", confidence=1.0, reasoning="Not candidate gender selection"
                )
            elif tag == "select" or el.get("options"):
                return FormFieldAction(
                    selector=selector, field_name="Gender", action_type="select_option",
                    value="Male" if "male" in user_gender and "female" not in user_gender else "Prefer not to say",
                    confidence=1.0, reasoning="Matched Gender Dropdown"
                )

        # 25. Python Proficiency
        if any(k in blob for k in ["proficiency in python", "python proficiency", "level of proficiency in python"]):
            return FormFieldAction(
                selector=selector, field_name="Python Proficiency", action_type="select_option",
                value="Full Proficiency", confidence=1.0, reasoning="Selected Python: Full Proficiency"
            )

        # 26. Other Programming Languages (Textarea)
        if any(k in blob for k in ["other computer programming languages", "programming languages that you have experience"]):
            langs_val = "TypeScript (Fully proficient), Go (Intermediate), Java (Intermediate), SQL (Fully proficient), C++ (Intermediate)"
            return FormFieldAction(
                selector=selector, field_name="Programming Languages", action_type="type_text",
                value=langs_val, confidence=1.0, reasoning="Provided Programming Languages & Proficiency"
            )

        # 27. Positions of Responsibility / Awards (Textarea)
        if any(k in blob for k in ["positions of responsibility", "awards / accomplishments", "awards", "accomplishments"]):
            resp_val = "Founder & Lead Engineer on high-throughput distributed event streaming engine processing 15,000+ events/sec. Dean's List / Faculty Honors at Georgia Institute of Technology."
            return FormFieldAction(
                selector=selector, field_name="Awards & Leadership", action_type="type_text",
                value=resp_val, confidence=1.0, reasoning="Provided Leadership & Notable Accomplishments"
            )

        # 28. Nationality & Visas (Textarea)
        if any(k in blob for k in ["nationality and", "nationality and, if applicable", "visas you hold", "nationality"]):
            nat_val = "U.S. Citizen. Authorized to work in the United States without restriction. Open to sponsorship/visa assistance for international office locations if applicable."
            return FormFieldAction(
                selector=selector, field_name="Nationality & Visas", action_type="type_text",
                value=nat_val, confidence=1.0, reasoning="Provided Nationality & Visa Status"
            )

        # 29. Referral from Employee
        if any(k in blob for k in ["referred by a", "referred by an employee", "employee referral"]):
            return FormFieldAction(
                selector=selector, field_name="Employee Referral", action_type="type_text",
                value="N/A", confidence=1.0, reasoning="Not referred by internal employee (N/A)"
            )

        # 30. How did you hear about vacancy
        if any(k in blob for k in ["how did you hear about this vacancy", "how did you hear", "hear about us"]):
            return FormFieldAction(
                selector=selector, field_name="Referral Source", action_type="select_option",
                value="LinkedIn", confidence=1.0, reasoning="Selected Source: LinkedIn"
            )

        # 31. Veteran Status (EEOC)
        if any(k in blob for k in ["veteran", "military"]):
            vet_val = eeoc.get("veteran_status", "I am not a protected veteran")
            return FormFieldAction(
                selector=selector, field_name="Veteran Status", action_type="select_option" if tag == "select" or el.get("options") else "type_text",
                value=vet_val, confidence=1.0, reasoning="Matched Veteran Status (Not a protected veteran)"
            )

        # 32. Disability Status (EEOC)
        if "disability" in blob:
            dis_val = eeoc.get("disability_status", "No, I do not have a disability")
            return FormFieldAction(
                selector=selector, field_name="Disability Status", action_type="select_option" if tag == "select" or el.get("options") else "type_text",
                value=dis_val, confidence=1.0, reasoning="Matched Disability Status (No disability)"
            )

        # 33. GDPR / Terms Agreement Checkbox
        if any(k in blob for k in ["gdpr", "consent", "terms", "agree", "privacy", "data processing", "content of the privacy notice"]):
            if el_type in ["checkbox", "radio"]:
                return FormFieldAction(
                    selector=selector, field_name="Consent Agreement", action_type="click_checkbox",
                    value="true", confidence=1.0, reasoning="Agreed to privacy/application terms"
                )

        return None
