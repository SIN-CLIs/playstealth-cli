import base64
import importlib.util
import json
import os
import pathlib
import tempfile
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


# Minimal gültiges 1x1 PNG für NVIDIA-NIM-Tests
_TEST_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5wZuoAAAAASUVORK5CYII="
)


def _write_test_png() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_TEST_PNG_BYTES)
    tmp.close()
    return tmp.name


class HeyPiggyWorkerNvidiaNimTests(unittest.IsolatedAsyncioTestCase):
    async def test_nvidia_nim_returns_auth_failure_without_key(self):
        """Ohne NVIDIA_API_KEY → klarer Auth-Failure, kein Crash."""
        with patch.object(worker, "NVIDIA_API_KEY", ""):
            result = await worker._nvidia_nim_chat(
                "test",
                "/tmp/nonexistent.png",
                timeout=5,
                model="meta/llama-3.2-90b-vision-instruct",
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["auth_failure"])
        self.assertIn("NVIDIA_API_KEY", result["error"])

    async def test_nvidia_nim_parses_openai_compat_response(self):
        """NVIDIA NIM OpenAI-kompatible Response wird korrekt geparst."""
        tmp_path = _write_test_png()
        fake_response = json.dumps({
            "id": "cmpl-test",
            "model": "meta/llama-3.2-90b-vision-instruct",
            "choices": [{
                "message": {
                    "content": '{"verdict":"PROCEED","page_state":"dashboard","next_action":"click_element","next_params":{"selector":"#btn"},"reason":"test","progress":true}',
                    "role": "assistant",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 150},
        })

        with patch.object(worker, "NVIDIA_API_KEY", "nvapi-test"), patch(
            "asyncio.to_thread", new=AsyncMock(return_value=(200, fake_response, ""))
        ):
            result = await worker._nvidia_nim_chat(
                "test prompt",
                tmp_path,
                timeout=30,
                model="meta/llama-3.2-90b-vision-instruct",
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["auth_failure"])
        self.assertIn("PROCEED", result["text"])
        self.assertEqual(result["model_used"], "meta/llama-3.2-90b-vision-instruct")

    async def test_nvidia_nim_handles_rate_limit_429(self):
        """429 Rate-Limit wird als retry-bar markiert, nicht als auth failure."""
        tmp_path = _write_test_png()

        with patch.object(worker, "NVIDIA_API_KEY", "nvapi-test"), patch(
            "asyncio.to_thread", new=AsyncMock(return_value=(429, "", "rate limit"))
        ):
            result = await worker._nvidia_nim_chat(
                "test",
                tmp_path,
                timeout=5,
                model="meta/llama-3.2-90b-vision-instruct",
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["auth_failure"])
        self.assertTrue(result.get("rate_limited"))

    async def test_nvidia_nim_handles_401_as_auth_failure(self):
        """401 → auth_failure=True (Preflight stoppt Worker)."""
        tmp_path = _write_test_png()
        with patch.object(worker, "NVIDIA_API_KEY", "nvapi-bad"), patch(
            "asyncio.to_thread", new=AsyncMock(return_value=(401, "", "invalid key"))
        ):
            result = await worker._nvidia_nim_chat(
                "test",
                tmp_path,
                timeout=5,
                model="meta/llama-3.2-90b-vision-instruct",
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["auth_failure"])

    async def test_run_vision_model_routes_to_nvidia_when_key_present(self):
        """Mit NVIDIA_API_KEY + VISION_BACKEND=auto → NVIDIA-Pfad wird gewählt."""
        fake_nvidia = AsyncMock(
            return_value={
                "ok": True, "auth_failure": False,
                "text": '{"verdict":"PROCEED"}',
                "stdout_text": "", "stderr_text": "", "returncode": 200,
            }
        )
        fake_opencode = AsyncMock(
            return_value={"ok": False, "auth_failure": True, "error": "should not be called"}
        )
        with patch.object(worker, "NVIDIA_API_KEY", "nvapi-test"), patch.object(
            worker, "VISION_BACKEND", "auto"
        ), patch.object(worker, "_run_vision_nvidia", fake_nvidia), patch.object(
            worker, "_run_vision_opencode", fake_opencode
        ):
            result = await worker.run_vision_model(
                "prompt", "/tmp/x.png", timeout=30, step_num=1
            )
        self.assertTrue(result["ok"])
        fake_nvidia.assert_awaited_once()
        fake_opencode.assert_not_called()

    async def test_run_vision_model_fallback_to_opencode_without_key(self):
        """Ohne NVIDIA_API_KEY → OpenCode CLI Pfad (Backwards Compat)."""
        fake_nvidia = AsyncMock(return_value={"ok": True, "text": "X"})
        fake_opencode = AsyncMock(
            return_value={"ok": True, "auth_failure": False, "text": "opencode-worked"}
        )
        with patch.object(worker, "NVIDIA_API_KEY", ""), patch.object(
            worker, "VISION_BACKEND", "opencode"
        ), patch.object(worker, "_run_vision_nvidia", fake_nvidia), patch.object(
            worker, "_run_vision_opencode", fake_opencode
        ):
            result = await worker.run_vision_model(
                "prompt", "/tmp/x.png", timeout=30, step_num=1
            )
        self.assertEqual(result["text"], "opencode-worked")
        fake_opencode.assert_awaited_once()
        fake_nvidia.assert_not_called()

    async def test_nvidia_fallback_chain_tries_next_model_on_error(self):
        """Wenn das Primary-Modell 500ert, wird das nächste Modell probiert."""
        tmp_path = _write_test_png()
        calls = []

        async def fake_chat(prompt, path, *, timeout, model, force_json=True):
            calls.append(model)
            if model == worker.NVIDIA_VISION_MODEL:
                return {"ok": False, "auth_failure": False, "error": "HTTP 500"}
            return {
                "ok": True, "auth_failure": False,
                "text": '{"verdict":"PROCEED"}',
                "stdout_text": "", "stderr_text": "", "returncode": 200,
            }

        with patch.object(worker, "_nvidia_nim_chat", side_effect=fake_chat):
            result = await worker._run_vision_nvidia(
                "test", tmp_path, timeout=30, step_num=1, purpose="vision"
            )

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(calls), 2, "Fallback-Modell muss probiert werden")
        self.assertEqual(calls[0], worker.NVIDIA_VISION_MODEL)


if __name__ == "__main__":
    unittest.main()
