"""Local browser fixtures; no real applications, API calls, or candidate data."""
import unittest

from playwright.sync_api import sync_playwright
from src.controller import ApplicationController
from src.executor import FormExecutor
from src.llm.prompts import FormFillPlan, FormFieldAction
from src.parsers import GenericParser
from src.parsers import WorkdayParser


class RecordingResolver:
    def __init__(self):
        self.batches = []

    def resolve_form(self, fields, profile, job_meta):
        self.batches.append([f["label"] for f in fields])
        actions = []
        for field in fields:
            value = {"Email": "test@example.com", "City": "Atlanta", "Country": "US"}.get(field["label"])
            if value:
                actions.append(FormFieldAction(
                    selector=field["selector"], field_name=field["label"],
                    action_type="select_option" if field["tag"] == "select" else "type_text",
                    value=value, confidence=1, reasoning="Fixture fact",
                ))
        return FormFillPlan(actions=actions)


class ControllerBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.resolver = RecordingResolver()
        self.executor = FormExecutor(self.page, {})

    def tearDown(self):
        self.page.close()

    def run_form(self, html, **kwargs):
        self.page.set_content(html)
        return ApplicationController(self.page, GenericParser(), self.resolver,
                                     self.executor, transition_timeout=.6, **kwargs).run({}, {})

    def test_discovers_conditional_field_without_resolving_email_twice(self):
        plan, results = self.run_form('''
            <label for="email">Email</label><input id="email" oninput="
            if (!document.getElementById('city')) {
              document.getElementById('extra').innerHTML = '<label for=city>City</label><input id=city>';
            }"><div id="extra"></div>''')
        self.assertEqual(self.resolver.batches, [["Email"], ["City"]])
        self.assertEqual(self.page.locator("#city").input_value(), "Atlanta")
        self.assertEqual(len(results["succeeded"]), 2)

    def test_generic_wizard_advances_and_never_clicks_submit(self):
        plan, results = self.run_form('''<div id="step">
          <h2>Contact</h2><label for=email>Email</label><input id=email>
          <button onclick="document.getElementById('step').innerHTML =
          '<h2>Location</h2><label for=city>City</label><input id=city><button id=submit>Submit</button>'">Next</button>
          </div>''')
        self.assertEqual(self.resolver.batches, [["Email"], ["City"]])
        self.assertEqual(len(results["succeeded"]), 2)
        self.assertTrue(self.page.locator("#submit").is_visible())

    def test_stalled_next_does_not_repeat_resolution_or_click(self):
        plan, results = self.run_form('''<label for=email>Email</label><input id=email>
          <button onclick="window.clicks=(window.clicks||0)+1">Next</button>''')
        self.assertIn("Stuck", plan.summary)
        self.assertEqual(self.page.evaluate("window.clicks"), 1)
        self.assertEqual(len(self.resolver.batches), 1)

    def test_login_handoff_never_invokes_resolver(self):
        plan, results = self.run_form('<label for=email>Email</label><input id=email><input type=password>')
        self.assertIn("Login required", plan.summary)
        self.assertEqual(self.resolver.batches, [])

    def test_control_rejecting_value_is_not_success(self):
        plan, results = self.run_form('''<label for=email>Email</label>
          <input id=email oninput="this.value=''">''')
        self.assertEqual(results["succeeded"], [])
        self.assertEqual(len(results["failed"]), 1)
        self.assertIn("could not be verified", plan.summary)

    def test_missing_custom_option_is_failure(self):
        self.page.set_content('''<button id=country aria-controls=choices>Choose country</button>
          <ul id=choices role=listbox><li role=option>Canada</li></ul>''')
        action = FormFieldAction(selector="#country", field_name="Country", action_type="select_option",
                                 value="US", confidence=1, reasoning="Fixture")
        result = self.executor.execute_plan(FormFillPlan(actions=[action]))
        self.assertEqual(result["succeeded"], [])
        self.assertEqual(len(result["failed"]), 1)

    def test_native_selection_is_read_back(self):
        _, results = self.run_form('''<label for=country>Country</label>
          <select id=country><option value="">Choose</option><option>US</option><option>Canada</option></select>''')
        self.assertEqual(results["succeeded"][0]["value"], "US")

    def test_workday_wizard_exceeding_old_six_step_limit(self):
        self.page.set_content('''<div id=step></div><script>
          let step = 1;
          function render() {
            document.getElementById('step').innerHTML = '<h2>Step '+step+'</h2>' +
              '<label for=email>Email</label><input id=email>' +
              (step < 8 ? '<button onclick="step++;render()">Next</button>' : '<button>Submit</button>');
          }
          render();
          </script>''')
        plan, results = ApplicationController(self.page, WorkdayParser(), self.resolver,
                                              self.executor, transition_timeout=.6).run({}, {})
        self.assertEqual(len(results["succeeded"]), 8)
        self.assertEqual(self.page.locator("h2").inner_text(), "Step 8")
        self.assertEqual(len(self.resolver.batches), 8)

    def test_hidden_checkbox_with_visible_label_remains_discoverable(self):
        self.page.set_content('<label>Consent<input id=consent type=checkbox style="display:none"></label>')
        controller = ApplicationController(self.page, GenericParser(), self.resolver, self.executor)
        fields = controller.observe()
        self.assertEqual([field["selector"] for field in fields], ["#consent"])
        action = FormFieldAction(selector="#consent", field_name="Consent", action_type="click_checkbox",
                                 value="true", confidence=1, reasoning="Fixture")
        results = self.executor.execute_plan(FormFillPlan(actions=[action]))
        self.assertEqual(len(results["succeeded"]), 1)


if __name__ == "__main__":
    unittest.main()
