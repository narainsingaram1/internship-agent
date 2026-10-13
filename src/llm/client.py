"""LLM Client supporting OpenRouter free endpoints with automatic fallback."""
import os
import re
import json
import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from .prompts import FormFillPlan, FORM_MAPPING_SYSTEM_PROMPT

load_dotenv()
logger = logging.getLogger(__name__)


def extract_json_from_llm(raw_text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON object from raw LLM output, handling markdown fences and surrounding prose."""
    if not raw_text or not isinstance(raw_text, str):
        return None

    cleaned = raw_text.strip()
    # Strip markdown ```json ... ``` blocks
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Regex search for first outer {...}
    match = re.search(r"(\{[\s\S]*\})", cleaned)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    return None


class LLMClient:
    def __init__(
        self,
        api_key: str = None,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "nvidia/nemotron-3-super-120b-a12b:free",
        fallback_model: str = "cohere/north-mini-code:free",
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            logger.warning("OPENROUTER_API_KEY is not set. Please set it in your .env file.")

        self.base_url = base_url
        self.default_model = default_model
        self.fallback_model = fallback_model

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "placeholder_key",
            timeout=25.0,
            default_headers={
                "HTTP-Referer": "https://github.com/internship-agent",
                "X-Title": "Internship Application Agent",
            },
        )

    def generate_fill_plan(
        self,
        form_elements: List[Dict[str, Any]],
        candidate_profile: Dict[str, Any],
        job_metadata: Dict[str, Any] = None,
    ) -> FormFillPlan:
        """Analyze extracted DOM form elements and return a structured fill plan."""
        job_metadata = job_metadata or {}

        user_content = {
            "job_context": {
                "title": job_metadata.get("title", "Unknown Role"),
                "company": job_metadata.get("company", "Unknown Company"),
                "url": job_metadata.get("url", ""),
            },
            "candidate_profile": candidate_profile,
            "form_fields": form_elements,
            "instruction": (
                "Analyze each field in `form_fields`. Map each field to the candidate profile. "
                "For custom questions (e.g. why this company, experience, technical questions), write a sharp, relevant answer. "
                "Return a JSON object conforming exactly to the FormFillPlan schema: "
                "{ 'actions': [ { 'selector': str, 'field_name': str, 'action_type': 'type_text'|'select_option'|'click_checkbox'|'upload_file'|'skip', 'value': str, 'confidence': float, 'reasoning': str } ], 'questions_to_review': [str], 'summary': str }"
            ),
        }

        # Active, verified free models on OpenRouter
        models_to_try = [
            self.default_model,
            self.fallback_model,
            "nvidia/nemotron-3-super-120b-a12b:free",
            "cohere/north-mini-code:free",
            "liquid/lfm-2.5-2.6b:free",
            "nvidia/nemotron-3.5-lightning:free",
            "dots-studio/dots-3-note-preview:free",
        ]
        # Deduplicate while preserving order
        unique_models = []
        for m in models_to_try:
            if m not in unique_models:
                unique_models.append(m)

        last_error = None

        for model in unique_models:
            try:
                logger.info(f"Invoking LLM for form field mapping via {model}...")
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": FORM_MAPPING_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"Form analysis payload:\n```json\n{json.dumps(user_content, indent=2)}\n```\nRespond ONLY with valid JSON.",
                        },
                    ],
                    max_tokens=1500,
                    temperature=0.2,
                )

                choice = response.choices[0]
                raw_content = choice.message.content
                # Handle reasoning models where output might reside in reasoning field
                if not raw_content and hasattr(choice.message, "reasoning"):
                    raw_content = choice.message.reasoning

                data = extract_json_from_llm(raw_content)
                if not data or not isinstance(data, dict):
                    raise ValueError(f"Model {model} did not return valid JSON: {str(raw_content)[:100]}")

                # Validate with Pydantic
                plan = FormFillPlan(**data)
                logger.info(f"Successfully generated plan with {len(plan.actions)} actions via {model}.")
                return plan

            except Exception as e:
                logger.warning(f"Error with model {model}: {e}. Trying fallback if available...")
                last_error = e

        # If all LLM calls fail, create a deterministic comprehensive fallback plan
        logger.error(f"All LLM attempts failed ({last_error}). Falling back to heuristic field matching.")
        return self._heuristic_fallback_plan(form_elements, candidate_profile)

    def _heuristic_fallback_plan(
        self, form_elements: List[Dict[str, Any]], profile: Dict[str, Any]
    ) -> FormFillPlan:
        """Deterministic fallback mapper for basic standard fields if API is offline."""
        personal = profile.get("personal", {})
        links = profile.get("links", {})
        edu = profile.get("education", {})
        work = profile.get("work_authorization", {})
        kb = profile.get("screening_knowledge_base", {})
        answers = kb.get("answers", {})

        actions = []
        needs_review = []

        for el in form_elements:
            name_lower = (el.get("name") or "").lower()
            label_lower = (el.get("label") or "").lower()
            combined = f"{name_lower} {label_lower}"
            selector = el.get("selector", "")

            # Personal
            if any(k in combined for k in ["first_name", "firstname", "first name"]) and not any(k in combined for k in ["last", "full"]):
                actions.append({"selector": selector, "field_name": "First Name", "action_type": "type_text", "value": personal.get("first_name", ""), "confidence": 0.95, "reasoning": "Fallback First Name"})
            elif any(k in combined for k in ["last_name", "lastname", "last name", "surname"]) and "first" not in combined:
                actions.append({"selector": selector, "field_name": "Last Name", "action_type": "type_text", "value": personal.get("last_name", ""), "confidence": 0.95, "reasoning": "Fallback Last Name"})
            elif "email" in combined:
                actions.append({"selector": selector, "field_name": "Email", "action_type": "type_text", "value": personal.get("email", ""), "confidence": 0.95, "reasoning": "Fallback Email"})
            elif any(k in combined for k in ["phone", "mobile", "tel"]):
                actions.append({"selector": selector, "field_name": "Phone", "action_type": "type_text", "value": str(personal.get("phone", "")), "confidence": 0.95, "reasoning": "Fallback Phone"})
            elif any(k in combined for k in ["school", "university", "college", "attending"]):
                actions.append({"selector": selector, "field_name": "University", "action_type": "type_text", "value": edu.get("school", ""), "confidence": 0.9, "reasoning": "Fallback University"})
            elif any(k in combined for k in ["major", "course", "field of study"]):
                actions.append({"selector": selector, "field_name": "Major", "action_type": "type_text", "value": edu.get("major", ""), "confidence": 0.9, "reasoning": "Fallback Major"})
            elif any(k in combined for k in ["gpa", "grade"]):
                actions.append({"selector": selector, "field_name": "GPA", "action_type": "type_text", "value": f"{edu.get('gpa', '3.89')} / 4.0", "confidence": 0.9, "reasoning": "Fallback GPA"})
            elif "resume" in combined:
                actions.append({"selector": selector, "field_name": "Resume Upload", "action_type": "upload_file", "value": "resume", "confidence": 0.95, "reasoning": "Fallback Resume"})
            elif any(k in combined for k in ["why are you interested", "why this", "cover letter"]):
                ans = answers.get("why_engineering", "I am passionate about quantitative software and building resilient systems.")
                actions.append({"selector": selector, "field_name": el.get("label", "Essay"), "action_type": "type_text", "value": ans.strip(), "confidence": 0.85, "reasoning": "Fallback Essay Answer"})
            elif any(k in combined for k in ["programming language", "proficiency"]):
                langs = ", ".join(kb.get("primary_languages", ["Python", "TypeScript", "Go"]))
                actions.append({"selector": selector, "field_name": el.get("label", "Languages"), "action_type": "type_text", "value": f"{langs} (Proficient)", "confidence": 0.85, "reasoning": "Fallback Skills"})
            elif any(k in combined for k in ["nationality", "visa", "authorization"]):
                actions.append({"selector": selector, "field_name": el.get("label", "Work Auth"), "action_type": "type_text", "value": "U.S. Citizen (Authorized to work in US without restriction)", "confidence": 0.85, "reasoning": "Fallback Work Auth"})
            elif "refer" in combined:
                actions.append({"selector": selector, "field_name": el.get("label", "Referral"), "action_type": "type_text", "value": "N/A", "confidence": 0.9, "reasoning": "Fallback Referral"})
            else:
                needs_review.append(el.get("label") or el.get("name") or selector)

        return FormFillPlan(
            actions=actions,
            questions_to_review=needs_review,
            summary=f"Heuristically matched {len(actions)} fields. {len(needs_review)} require review.",
        )
