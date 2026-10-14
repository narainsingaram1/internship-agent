"""Exercise web pause/resume ownership without opening sites or touching user data."""
import asyncio
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from src.llm.prompts import FormFillPlan
from web import server


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_submit_and_abort_stay_on_browser_owner_thread(self):
        engine = server.EngineSession()
        engine.loop = asyncio.get_running_loop()
        engine.worker_active = True
        engine.state = "running"
        calls = []
        page = Mock()
        session = Mock()
        session.navigate.return_value = page
        session.stop.side_effect = lambda: calls.append(("stop", threading.get_ident()))
        controller = Mock()
        controller.page = page

        def run(*args):
            calls.append(("run", threading.get_ident()))
            return FormFillPlan(summary="Paused"), {"succeeded": [], "failed": [], "skipped": []}

        controller.run.side_effect = run
        reviewer = Mock()
        reviewer.submit_application.side_effect = lambda: calls.append(("submit", threading.get_ident())) or False
        with ExitStack() as stack:
            for name, replacement in {
                "engine": engine, "BrowserSession": Mock(return_value=session),
                "ApplicationController": Mock(return_value=controller),
                "MemoryStore": Mock(), "LLMClient": Mock(), "TwoTierResolver": Mock(),
                "FormExecutor": Mock(), "get_profile": Mock(return_value={}),
            }.items():
                stack.enter_context(patch.object(server, name, replacement))
            stack.enter_context(patch.object(server.GenericParser, "extract_job_metadata", return_value={"title": "Fixture"}))
            stack.enter_context(patch("src.cli.HITLReviewer", return_value=reviewer))
            thread = threading.Thread(target=server.run_autofill_worker, args=("https://fixture.test/job", True), daemon=True)
            thread.start()

            async def wait_review():
                for _ in range(100):
                    if engine.state == "review":
                        return
                    await asyncio.sleep(.01)
                self.fail("Worker did not pause")

            await wait_review()
            result = await server.resume_application()
            self.assertEqual(result["status"], "resuming")
            await wait_review()
            self.assertEqual(len([c for c in calls if c[0] == "run"]), 2)
            result = await server.submit_application()
            self.assertEqual(result["status"], "unconfirmed")
            self.assertEqual(engine.state, "review")
            result = await server.abort_application()
            self.assertEqual(result["status"], "aborted")
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertFalse(engine.worker_active)
        self.assertEqual({thread_id for _, thread_id in calls}, {thread.ident})
        self.assertNotEqual(thread.ident, threading.get_ident())

    async def test_commands_reject_an_application_that_is_still_running(self):
        engine = server.EngineSession()
        engine.worker_active = True
        engine.state = "running"
        with patch.object(server, "engine", engine):
            with self.assertRaises(server.HTTPException) as error:
                await server.resume_application()
            self.assertEqual(error.exception.status_code, 409)
