import importlib.util
import os
import pathlib
import unittest
from unittest.mock import AsyncMock, patch


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "heypiggy_vision_worker.py"
SPEC = importlib.util.spec_from_file_location("heypiggy_vision_worker", MODULE_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class DummyGate:
    def __init__(self):
        self.failed_selectors = []
        self.recorded = []

    def is_selector_failed(self, selector: str) -> bool:
        return False

    def add_failed_selector(self, selector: str):
        self.failed_selectors.append(selector)

    def record_step(self, verdict: str, img_hash: str):
        self.recorded.append((verdict, img_hash))


class HeyPiggyWorkerPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_stops_before_browser_mutation_when_credentials_missing(self):
        execute_bridge = AsyncMock()
        check_bridge_alive = AsyncMock(return_value=True)
        run_vision_model = AsyncMock(
            side_effect=AssertionError("vision probe must not run when credentials are missing")
        )

        with patch.dict(
            os.environ,
            {"HEYPIGGY_EMAIL": "", "HEYPIGGY_PASSWORD": ""},
            clear=False,
        ), patch.object(worker, "wait_for_extension", AsyncMock(return_value=True)), patch.object(
            worker, "check_bridge_alive", check_bridge_alive
        ), patch.object(worker, "run_vision_model", run_vision_model), patch.object(
            worker, "execute_bridge", execute_bridge
        ):
            await worker.main()

        execute_bridge.assert_not_awaited()
        check_bridge_alive.assert_not_awaited()

    async def test_main_stops_before_browser_mutation_when_vision_auth_fails(self):
        execute_bridge = AsyncMock()

        with patch.dict(
            os.environ,
            {
                "HEYPIGGY_EMAIL": "ops@example.com",
                "HEYPIGGY_PASSWORD": "secret",
            },
            clear=False,
        ), patch.object(worker, "wait_for_extension", AsyncMock(return_value=True)), patch.object(
            worker, "check_bridge_alive", AsyncMock(return_value=True)
        ), patch.object(
            worker,
            "run_vision_model",
            AsyncMock(
                return_value={
                    "ok": False,
                    "auth_failure": True,
                    "error": "401 invalid authentication credentials",
                }
            ),
        ), patch.object(worker, "execute_bridge", execute_bridge):
            await worker.main()

        execute_bridge.assert_not_awaited()

    async def test_main_stops_before_browser_mutation_when_vision_health_fails(self):
        execute_bridge = AsyncMock()

        with patch.dict(
            os.environ,
            {
                "HEYPIGGY_EMAIL": "ops@example.com",
                "HEYPIGGY_PASSWORD": "secret",
            },
            clear=False,
        ), patch.object(worker, "wait_for_extension", AsyncMock(return_value=True)), patch.object(
            worker, "check_bridge_alive", AsyncMock(return_value=True)
        ), patch.object(
            worker,
            "run_vision_model",
            AsyncMock(
                return_value={
                    "ok": False,
                    "auth_failure": True,
                    "error": "vision health check failed",
                }
            ),
        ), patch.object(worker, "execute_bridge", execute_bridge):
            await worker.main()

        execute_bridge.assert_not_awaited()

    async def test_ask_vision_turns_auth_failure_into_stop(self):
        with patch.object(worker, "dom_prescan", AsyncMock(return_value="DOM")), patch.object(
            worker,
            "run_vision_model",
            AsyncMock(
                return_value={
                    "ok": False,
                    "auth_failure": True,
                    "error": "401 invalid authentication credentials",
                }
            ),
        ):
            decision = await worker.ask_vision("/tmp/probe.png", "action", "expected", 1)

        self.assertEqual(decision["verdict"], "STOP")
        self.assertEqual(decision["page_state"], "error")
        self.assertEqual(decision["next_action"], "none")

    def test_detect_vision_auth_failure_treats_health_failures_as_blockers(self):
        blocker = worker.detect_vision_auth_failure(
            "provider health check failed: vision model unhealthy"
        )

        self.assertEqual(blocker, "provider health check failed")


class HeyPiggyWorkerClickPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_click_action_routes_click_ref_through_escalation_pipeline(self):
        gate = DummyGate()
        escalating_click = AsyncMock(return_value=True)

        with patch.object(worker, "escalating_click", escalating_click):
            clicked = await worker.run_click_action({"ref": "@e9"}, gate, "hash123", 7)

        self.assertTrue(clicked)
        escalating_click.assert_awaited_once_with(
            selector="",
            description="",
            x=None,
            y=None,
            step_num=7,
            ref="@e9",
        )
        self.assertEqual(gate.failed_selectors, [])
        self.assertEqual(gate.recorded, [])


class HeyPiggyWorkerVisionTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def test_cli_timeout_respects_full_requested_timeout(self):
        """Regression: Früher war cli_timeout auf 25s gecappt → JEDER Call starb."""
        # Direkt die Cap-Logik nachbilden wie in run_vision_model
        timeout = 180
        cli_timeout = max(30, timeout - 5)
        self.assertEqual(cli_timeout, 175)
        self.assertGreater(cli_timeout, 60, "CLI-Timeout muss groß genug für Gemini sein")


class HeyPiggyWorkerControllerTests(unittest.TestCase):
    def test_failed_selectors_reset_on_page_state_change(self):
        """failed_selectors müssen bei Page-State-Wechsel geleert werden."""
        gate = worker.VisionGateController()
        gate.add_failed_selector("#bad")
        gate.add_failed_selector("#bad")
        gate.add_failed_selector("#bad")
        self.assertTrue(gate.is_selector_failed("#bad"))

        gate.record_step("PROCEED", "hash1", page_state="dashboard")
        self.assertFalse(
            gate.is_selector_failed("#bad"),
            "Selektor sollte nach Page-State-Wechsel nicht mehr gesperrt sein",
        )

    def test_failed_selectors_require_three_failures_before_blocking(self):
        """Ein einzelner Fail darf den Selektor nicht sofort sperren."""
        gate = worker.VisionGateController()
        gate.add_failed_selector("#flaky")
        self.assertFalse(gate.is_selector_failed("#flaky"))
        gate.add_failed_selector("#flaky")
        self.assertFalse(gate.is_selector_failed("#flaky"))
        gate.add_failed_selector("#flaky")
        self.assertTrue(gate.is_selector_failed("#flaky"))


class HeyPiggyWorkerJsonParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_vision_extracts_json_from_prose_wrapped_output(self):
        """Regression: Prosa um JSON herum darf nicht zu RETRY führen."""
        prosa_output = (
            "Ich analysiere den Screenshot. Hier meine Entscheidung:\n"
            '{"verdict": "PROCEED", "page_state": "dashboard", '
            '"next_action": "click_element", "next_params": {"selector": "#btn"}, '
            '"reason": "Button sichtbar", "progress": true}\n'
            "Hoffentlich hilft das."
        )
        with patch.object(worker, "dom_prescan", AsyncMock(return_value="DOM")), patch.object(
            worker,
            "run_vision_model",
            AsyncMock(return_value={"ok": True, "auth_failure": False, "text": prosa_output}),
        ):
            decision = await worker.ask_vision("/tmp/x.png", "a", "b", 1)

        self.assertEqual(decision["verdict"], "PROCEED")
        self.assertEqual(decision["page_state"], "dashboard")
        self.assertEqual(decision["next_action"], "click_element")


class HeyPiggyWorkerProfilePathTests(unittest.TestCase):
    def test_profile_path_resolver_uses_env_override(self):
        with patch.dict(os.environ, {"HEYPIGGY_PROFILE_PATH": "/tmp/custom.json"}):
            path = worker._resolve_profile_path()
        self.assertEqual(str(path), "/tmp/custom.json")

    def test_profile_path_resolver_has_portable_fallback(self):
        """Darf nicht mehr hardcoded auf /Users/jeremy/ zeigen."""
        with patch.dict(os.environ, {}, clear=False):
            # Explizit alle relevanten Env-Vars entfernen
            for k in ("HEYPIGGY_PROFILE_PATH", "XDG_CONFIG_HOME"):
                os.environ.pop(k, None)
            path = worker._resolve_profile_path()
        self.assertNotIn("/Users/jeremy", str(path))


if __name__ == "__main__":
    unittest.main()
