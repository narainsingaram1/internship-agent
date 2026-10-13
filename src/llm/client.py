"""LLM Client supporting OpenRouter free endpoints with automatic fallback."""
import os
import json
import logging
from typing import Dict, Any, List
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from .prompts import FormFillPlan, FORM_MAPPING_SYSTEM_PROMPT

load_dotenv()
logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        api_key: str = None,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "google/gemini-2.0-flash-exp:free",
        fallback_model: str = "meta-llama/llama-3.3-70b-instruct:free",
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
                "For custom questions (e.g. why this company), write a sharp, relevant answer. "
                "Return a JSON object conforming exactly to the FormFillPlan schema: "
                "{ 'actions': [ { 'selector': str, 'field_name': str, 'action_type': 'type_text'|'select_option'|'click_checkbox'|'upload_file'|'skip', 'value': str, 'confidence': float, 'reasoning': str } ], 'questions_to_review': [str], 'summary': str }"
            ),
        }

        # Try default model first, fallback if error occurs
        models_to_try = [self.default_model, self.fallback_model]
        last_error = None

        for model in models_to_try:
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
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )

                raw_content = response.choices[0].message.content
                data = json.loads(raw_content)

                # Validate with Pydantic
                plan = FormFillPlan(**data)
                return plan

            except Exception as e:
                logger.warning(f"Error with model {model}: {e}. Trying fallback if available...")
                last_error = e

        # If all LLM calls fail (e.g. invalid key or network error), create a deterministic heuristic plan
        logger.error(f"All LLM attempts failed ({last_error}). Falling back to heuristic field matching.")
        return self._heuristic_fallback_plan(form_elements, candidate_profile)

    def _heuristic_fallback_plan(
        self, form_elements: List[Dict[str, Any]], profile: Dict[str, Any]
    ) -> FormFillPlan:
        """Deterministic fallback mapper for basic standard fields if API is offline."""
        personal = profile.get("personal", {})
        links = profile.get("links", {})
        actions = []
        needs_review = []

        field_map = {
            "first_name": personal.get("first_name", ""),
            "last_name": personal.get("last_name", ""),
            "email": personal.get("email", ""),
            "phone": personal.get("phone", ""),
            "linkedin": links.get("linkedin", ""),
            "github": links.get("github", ""),
            "website": links.get("portfolio", ""),
            "portfolio": links.get("portfolio", ""),
        }

        for el in form_elements:
            name_lower = (el.get("name") or "").lower()
            label_lower = (el.get("label") or "").lower()
            combined = f"{name_lower} {label_lower}"
            selector = el.get("selector", "")

            matched_key = None
            for k in field_map:
                if k in combined:
                    matched_key = k
                    break

            if matched_key and field_map[matched_key]:
                actions.append(
                    {
                        "selector": selector,
                        "field_name": el.get("label") or el.get("name"),
                        "action_type": "type_text",
                        "value": field_map[matched_key],
                        "confidence": 0.85,
                        "reasoning": f"Matched heuristic key '{matched_key}'",
                    }
                )
            elif "resume" in combined:
                actions.append(
                    {
                        "selector": selector,
                        "field_name": "Resume Upload",
                        "action_type": "upload_file",
                        "value": "resume",
                        "confidence": 0.9,
                        "reasoning": "Detected resume file input",
                    }
                )
            else:
                needs_review.append(el.get("label") or el.get("name") or selector)

        return FormFillPlan(
            actions=actions,
            questions_to_review=needs_review,
            summary=f"Heuristically matched {len(actions)} fields. {len(needs_review)} require review.",
        )
