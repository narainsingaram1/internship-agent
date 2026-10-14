"""Pydantic schemas and prompts for LLM form mapping and question answering."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class FormFieldAction(BaseModel):
    selector: str = Field(..., description="Unique CSS selector or element identifier")
    field_name: str = Field(..., description="Human-readable label or description of the field")
    action_type: Literal["type_text", "select_option", "click_checkbox", "upload_file", "skip"] = Field(
        ..., description="The action to execute on this field"
    )
    value: str = Field(..., description="The value to type, option label to select, or file type to upload")
    confidence: float = Field(..., description="Confidence score from 0.0 to 1.0")
    reasoning: str = Field(..., description="Brief explanation of why this value was selected")


class FormFillPlan(BaseModel):
    actions: List[FormFieldAction] = Field(default_factory=list, description="Ordered list of field actions to execute")
    questions_to_review: List[str] = Field(
        default_factory=list, description="Names of questions requiring human review (confidence < 0.85 or essay answers)"
    )
    summary: Optional[str] = Field(default="", description="Short summary of what was matched vs skipped")


class ScreeningAnswer(BaseModel):
    question: str
    answer: str
    tone: str = "direct, humble, and competent engineering student"
    confidence: float


class NavigationDecision(BaseModel):
    action: Literal["click", "wait", "handoff"]
    control_id: Optional[int] = None
    reason: str = Field(default="", max_length=200)


FORM_MAPPING_SYSTEM_PROMPT = """You are an elite, precision job application autofill assistant.
Your job is to match form inputs extracted from an internship application page against a candidate's structured profile.

Rules:
1. STRICT TRUTH: Never invent facts, degrees, GPAs, or work experience that are not in the candidate's profile.
2. ACCURATE VALUE SELECTION:
   - For dropdowns (`select_option`), you must pick the option string that best matches the available choices.
   - For phone numbers, format cleanly (e.g. standard digits or dashed).
   - For file uploads, specify whether it's 'resume' or 'transcript'.
3. WORK AUTHORIZATION & SPONSORSHIP:
   - Pay extreme attention to: "Are you legally authorized to work in the US?" -> Matches profile.work_authorization.authorized_in_us.
   - "Will you now or in the future require visa sponsorship?" -> Matches profile.work_authorization.requires_sponsorship_now / requires_sponsorship_future.
4. EEOC / DEMOGRAPHICS:
   - Match exactly the candidate's preferences in `demographics_eeoc` (e.g., 'Decline to self identify' if specified).
5. CUSTOM SHORT ESSAYS:
   - If there is a prompt like "Why do you want to work at [Company]?" or "Tell us about a project", synthesize a concise, authentic, tailored 2-4 sentence response using the candidate's background and projects. Avoid clichés, cringe corporate buzzwords, and AI filler words.
6. JSON COMPLIANCE:
   - Always return valid JSON conforming to the requested schema.
"""
