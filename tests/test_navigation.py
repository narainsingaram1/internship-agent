"""Navigation journeys run against in-memory pages, with external requests intercepted."""
import json
import unittest
from unittest.mock import Mock

from playwright.sync_api import sync_playwright
from src.controller import ApplicationController
from src.executor import FormExecutor
from src.llm.client import LLMClient
from src.llm.prompts import NavigationDecision
from src.navigation import ApplicationNavigator
from src.parsers import GenericParser, WorkdayParser
from src.cli import HITLReviewer
from test_controller import RecordingResolver


FORM = '<form id=application_form><label for=email>Email</label><input id=email><button>Submit Application</button></form>'


class FakeNavigationAI:
    def __init__(self, action="click", label="Begin candidate journey"):
        self.snapshots = []
        self.action = action
        self.label = label

    def choose_navigation(self, snapshot, goal):
        self.snapshots.append(snapshot)
        control = next((c for c in snapshot["controls"] if c["label"] == self.label), None)
        return NavigationDecision(action=self.action, control_id=control["id"] if control else 999)


class NavigationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route("**/*", lambda route: route.fulfill(body=FORM, content_type="text/html"))
        self.page = self.context.new_page()
        self.ai = FakeNavigationAI()

    def tearDown(self):
        self.context.close()

    def controller(self, parser=None):
        resolver = RecordingResolver()
        resolver.llm = self.ai
        return ApplicationController(self.page, parser or GenericParser(), resolver,
                                     FormExecutor(self.page, {}), transition_timeout=.7)

    def test_workday_landing_method_choice_and_form_use_zero_ai(self):
        self.page.set_content('''<h1>Software Intern</h1><input type=search aria-label="Search jobs">
          <div id=body><button data-automation-id=adventureButton onclick="
          document.getElementById('body').innerHTML =
          '<button data-automation-id=applyManually onclick=showForm()>Apply Manually</button>'">Apply</button></div>
          <script>function showForm() {document.getElementById('body').innerHTML =
          '<form id=application_form><label for=email>Email</label><input id=email><button>Submit Application</button></form>';}</script>''')
        controller = self.controller(WorkdayParser())
        plan, results = controller.run({}, {})
        self.assertEqual(len(results["succeeded"]), 1)
        self.assertEqual(results["navigation"]["clicks"], 2)
        self.assertEqual(results["navigation"]["ai_calls"], 0)
        self.assertEqual(self.ai.snapshots, [])

    def test_unfamiliar_landing_uses_one_small_ai_decision(self):
        self.page.set_content('''<h1>Software Intern</h1><button onclick="location.href='https://fixture.test/form'">
          Begin candidate journey</button>''')
        controller = self.controller()
        _, results = controller.run({"private_fact": "must not be sent"}, {})
        self.assertEqual(len(results["succeeded"]), 1)
        self.assertEqual(results["navigation"]["ai_calls"], 1)
        payload = json.dumps(self.ai.snapshots)
        self.assertNotIn("private_fact", payload)
        self.assertNotIn("must not be sent", payload)
        self.assertLess(len(payload), 6000)

    def test_popup_is_followed_and_filled(self):
        self.page.set_content('<h1>Intern</h1><button onclick="window.open(\'https://fixture.test/form\')">Apply</button>')
        controller = self.controller()
        _, results = controller.run({}, {})
        self.assertIsNot(controller.page, self.page)
        self.assertEqual(controller.page.locator("#email").input_value(), "test@example.com")
        self.assertEqual(len(results["succeeded"]), 1)

    def test_redirect_redetects_workday(self):
        self.page.set_content('<h1>Intern</h1><a href="https://company.myworkdayjobs.com/form">Apply</a>')
        controller = self.controller()
        meta = {}
        controller.run({}, meta)
        self.assertIsInstance(controller.parser, WorkdayParser)
        self.assertEqual(meta["platform"], "workday")

    def test_stalled_apply_clicked_once_with_no_ai_retry(self):
        self.page.set_content('<h1>Intern</h1><button onclick="window.clicks=(window.clicks||0)+1">Apply</button>')
        controller = self.controller()
        plan, results = controller.run({}, {})
        self.assertIn("stalled", plan.summary)
        self.assertEqual(self.page.evaluate("window.clicks"), 1)
        self.assertEqual(results["navigation"]["ai_calls"], 0)

    def test_blocked_apply_can_recover_through_one_ai_overlay_decision(self):
        self.page.set_content('''<h1>Intern</h1><a href="https://fixture.test/form">Apply</a>
          <div id=notice style="position:fixed;inset:0;background:white;z-index:99">
            <button onclick="document.getElementById('notice').remove()">Close notice</button>
          </div>''')
        self.ai.label = "Close notice"
        controller = self.controller()
        _, results = controller.run({}, {})
        self.assertEqual(len(results["succeeded"]), 1)
        self.assertEqual(results["navigation"]["ai_calls"], 1)

    def test_next_can_cross_an_intermediate_page_with_no_fields(self):
        self.page.set_content('''<div id=step><h2>Contact</h2><label for=email>Email</label><input id=email>
          <button onclick="document.getElementById('step').innerHTML =
          '<h2>Continue application</h2><a href=https://fixture.test/form>Apply Manually</a>'">Next</button></div>''')
        controller = self.controller()
        _, results = controller.run({}, {})
        self.assertEqual(len(results["succeeded"]), 2)
        self.assertEqual(results["navigation"]["ai_calls"], 0)

    def test_completion_progress_indicator_is_not_a_loading_spinner(self):
        self.page.set_content('<div role=progressbar aria-valuenow=50></div>' + FORM)
        controller = self.controller()
        _, results = controller.run({}, {})
        self.assertEqual(len(results["succeeded"]), 1)

    def test_ai_cannot_click_unknown_control(self):
        self.page.set_content('<h1>Intern</h1><button>Unfamiliar route</button>')
        controller = self.controller()
        plan, results = controller.run({}, {})
        self.assertIn("unavailable or protected", plan.summary)
        self.assertEqual(results["navigation"]["clicks"], 0)

    def test_apply_label_on_final_submit_is_never_clicked(self):
        self.page.set_content('<form id=application_form onsubmit="window.sent=true;return false">'
                              '<label for=email>Email</label><input id=email><button>Apply</button></form>')
        controller = self.controller()
        _, results = controller.run({}, {})
        self.assertEqual(len(results["succeeded"]), 1)
        self.assertFalse(self.page.evaluate("Boolean(window.sent)"))
        self.assertEqual(results["navigation"]["clicks"], 0)

    def test_ai_wait_decision_not_repeated_on_unchanged_page(self):
        self.page.set_content('<h1>Intern</h1><button>Begin candidate journey</button>')
        self.ai.action = "wait"
        controller = self.controller()
        plan, results = controller.run({}, {})
        self.assertEqual(results["navigation"]["ai_calls"], 1)
        self.assertIn("unchanged", plan.summary)

    def test_three_call_budget_across_changing_unknown_pages(self):
        self.page.set_content('''<h1 id=title>Stage 0</h1><button onclick="
          document.getElementById('title').innerText='Stage '+(++window.stage)">Begin candidate journey</button>
          <script>window.stage=0</script>''')
        controller = self.controller()
        plan, results = controller.run({}, {})
        self.assertEqual(results["navigation"]["ai_calls"], 3)
        self.assertEqual(self.page.evaluate("window.stage"), 3)
        self.assertIn("budget exhausted", plan.summary)

    def test_cross_origin_iframe_form_is_filled_in_frame_scope(self):
        self.page.set_content('<h1>Intern</h1><iframe src="https://fixture.test/form"></iframe>')
        self.page.frame_locator("iframe").locator("#email").wait_for()
        controller = self.controller()
        _, results = controller.run({}, {})
        self.assertEqual(self.page.frame_locator("iframe").locator("#email").input_value(), "test@example.com")
        self.assertEqual(len(results["succeeded"]), 1)

    def test_submission_click_without_confirmation_is_not_success(self):
        self.page.set_content('<button onclick="window.clicked=true">Submit Application</button>')
        self.assertFalse(HITLReviewer(self.page).submit_application(confirmation_timeout=.3))
        self.assertTrue(self.page.evaluate("window.clicked"))

    def test_submission_requires_visible_receipt(self):
        self.page.set_content('<button onclick="document.body.innerHTML=\'<h1>Your application has been submitted</h1>\'">Submit Application</button>')
        self.assertTrue(HITLReviewer(self.page).submit_application(confirmation_timeout=.3))

    def test_next_is_not_mistaken_for_submit(self):
        self.page.set_content('<form><button onclick="window.clicked=true">Next</button></form>')
        self.assertFalse(HITLReviewer(self.page).submit_application(confirmation_timeout=.3))
        self.assertFalse(self.page.evaluate("Boolean(window.clicked)"))


class NavigationClientTests(unittest.TestCase):
    def test_single_request_has_no_sdk_retries_and_tracks_usage(self):
        llm = LLMClient(api_key="fixture-key")
        llm.client = Mock()
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message.content = '{"action":"click","control_id":2}'
        response.usage.prompt_tokens = 110
        response.usage.completion_tokens = 12
        llm.client.with_options.return_value.chat.completions.create.return_value = response
        decision = llm.choose_navigation({"controls": [{"id": 2, "label": "Begin"}]})
        self.assertEqual(decision.control_id, 2)
        llm.client.with_options.assert_called_once_with(max_retries=0, timeout=12)
        kwargs = llm.client.with_options.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 160)
        self.assertEqual(llm.navigation_usage, {"calls": 1, "prompt_tokens": 110, "completion_tokens": 12})
