import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.executor import OptionMatcher
from src.llm.client import LLMClient
from src.llm.prompts import FormFillPlan, FormFieldAction
from src.memory import MemoryStore
from src.resolver import TwoTierResolver


class ResolutionTests(unittest.TestCase):
    def test_common_education_fields_do_not_call_ai(self):
        llm, memory = Mock(), Mock()
        memory.get_answer.return_value = None
        resolver = TwoTierResolver(memory, llm)
        plan = resolver.resolve_form([
            {"selector": "#school", "label": "School", "tag": "select"},
            {"selector": "#major", "label": "Major", "tag": "input"},
        ], {"education": {"school": "MIT", "major": "Mathematics"}})
        self.assertEqual([action.value for action in plan.actions], ["MIT", "Mathematics"])
        llm.generate_fill_plan.assert_not_called()

    def test_school_topic_cannot_replace_a_different_university(self):
        self.assertIsNone(OptionMatcher.find_best_option("MIT", "School", ["Georgia Tech", "Other"]))
        self.assertEqual(OptionMatcher.find_best_option("Georgia Institute of Technology", "School", ["Georgia Tech"]), "Georgia Tech")

    def test_shared_words_do_not_choose_opposite_answer(self):
        self.assertIsNone(OptionMatcher.find_best_option("I require sponsorship", "Sponsorship", ["I do not require sponsorship"]))

    def test_profile_projection_omits_unrelated_facts(self):
        profile = {"work_authorization": {"authorized_in_us": True}, "personal": {"email": "private"},
                   "demographics_eeoc": {"gender": "private"}, "documents": {"resume_path": "private"}}
        self.assertEqual(LLMClient.relevant_profile([{"label": "Will you require sponsorship?"}], profile),
                         {"work_authorization": {"authorized_in_us": True}})

    def test_failure_retries_are_bounded_and_never_invent_facts(self):
        llm = LLMClient(api_key="fixture")
        llm.client = Mock()
        llm.client.chat.completions.create.side_effect = RuntimeError("Fixture failure")
        fields = [{"label": "Nationality", "selector": "#nationality"}]
        for _ in range(4):
            plan = llm.generate_fill_plan(fields, {})
            self.assertEqual(plan.actions, [])
            self.assertIn("Nationality", plan.questions_to_review)
        self.assertEqual(llm.client.chat.completions.create.call_count, 4)

    def test_only_approved_global_facts_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(str(Path(directory) / "memory.json"))
            memory.save_answer("Why this company?", "Draft employer answer")
            self.assertIsNone(memory.get_answer("Why this company?"))
            memory.save_answer("Can you relocate?", "Yes", source="approved", scope="global_fact")
            self.assertEqual(memory.get_answer("Can you relocate?", ["Yes", "No"]), "Yes")
            self.assertIsNone(memory.get_answer("Can you relocate?", ["No"]))

    def test_resolver_never_persists_unapproved_generation(self):
        memory, llm = Mock(), Mock()
        memory.get_answer.return_value = None
        llm.generate_fill_plan.return_value = FormFillPlan(actions=[FormFieldAction(
            selector="#essay", field_name="Why this company?", action_type="type_text", value="Draft",
            confidence=.8, reasoning="Fixture",
        )])
        resolver = TwoTierResolver(memory, llm)
        resolver.resolve_form([{"selector": "#essay", "label": "Why this company?", "tag": "textarea"}], {})
        memory.save_answer.assert_not_called()

    def test_removed_hardcoded_accomplishment_does_not_become_a_fact(self):
        resolver = TwoTierResolver(Mock(), Mock())
        field = {"selector": "#awards", "label": "Awards / accomplishments", "tag": "textarea"}
        self.assertIsNone(resolver._match_deterministic(field, {}))
