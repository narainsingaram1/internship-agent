"""Continuous Memory Store for remembering answers across job applications."""
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class MemoryStore:
    """Persists questions and verified answers so the agent learns continuously."""

    def __init__(self, storage_path: str = "data/memory_store.json"):
        self.storage_path = storage_path
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        if not os.path.exists(self.storage_path):
            with open(self.storage_path, "w") as f:
                json.dump({}, f)

    def _normalize_key(self, text: str) -> str:
        """Normalize question text for fuzzy comparison."""
        cleaned = "".join(c.lower() for c in text if c.isalnum() or c.isspace())
        return " ".join(cleaned.split())

    def load_all(self) -> Dict[str, Any]:
        """Load all remembered questions and answers."""
        try:
            with open(self.storage_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading memory store: {e}")
            return {}

    def get_answer(self, question_text: str, options: List[str] = None) -> Optional[str]:
        """Check if an answer exists for this question in memory."""
        if not question_text:
            return None

        memories = self.load_all()
        norm_q = self._normalize_key(question_text)
        for entry in memories.values():
            if entry.get("source") not in {"human_verified", "user_verified", "human_confirmed", "approved"}:
                continue
            if entry.get("scope") != "global_fact":
                continue  # Company-specific essays must not be reused as universal answers.
            if norm_q == self._normalize_key(entry.get("question", "")):
                answer = entry.get("answer", "")
                return self._match_option(answer, options) if options else answer
        return None

    def _match_option(self, target_answer: str, options: List[str]) -> Optional[str]:
        """Match remembered answer to one of the available dropdown/radio choices."""
        target_lower = target_answer.lower()
        for opt in options:
            if target_lower == opt.lower():
                return opt
        return None

    def save_answer(self, question_text: str, answer: str, source: str = "ai_generated", scope: str = "draft"):
        """Save a newly generated or human-confirmed question and answer."""
        if not question_text or not answer:
            return

        memories = self.load_all()
        norm_key = self._normalize_key(question_text)[:60]

        existing = memories.get(norm_key, {})
        times_used = existing.get("times_used", 0) + 1

        memories[norm_key] = {
            "question": question_text.strip(),
            "answer": answer.strip(),
            "source": source,
            "scope": scope,
            "times_used": times_used,
            "updated_at": datetime.now().isoformat(),
        }

        try:
            with open(self.storage_path, "w") as f:
                json.dump(memories, f, indent=2)
            logger.info(f"Saved memory for: '{question_text[:40]}...'")
        except Exception as e:
            logger.error(f"Error saving to memory store: {e}")

    def get_list(self) -> List[Dict[str, Any]]:
        """Return list of all memories for the UI."""
        memories = self.load_all()
        items = list(memories.values())
        items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return items
