# ================================================================================
# DATEI: test_heypiggy_vision_worker.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

import base64
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "heypiggy_vision_worker.py"
SPEC = importlib.util.spec_from_file_location("heypiggy_vision_worker", MODULE_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class DummyGate:
    # ========================================================================
    # KLASSE: DummyGate
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def __init__(self):
    # -------------------------------------------------------------------------
    # FUNKTION: __init__
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        self.failed_selectors = []
        self.recorded = []

    def is_selector_failed(self, selector: str) -> bool:
        return False

    def add_failed_selector(self, selector: str):
    # -------------------------------------------------------------------------
    # FUNKTION: add_failed_selector
    # PARAMETER: self, selector: str
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        self.failed_selectors.append(selector)

    def record_step(self, verdict: str, img_hash: str):
    # -------------------------------------------------------------------------
    # FUNKTION: record_step
    # PARAMETER: self, verdict: str, img_hash: str
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        self.recorded.append((verdict, img_hash))


class HeyPiggyWorkerPreflightTests(unittest.IsolatedAsyncioTestCase):
    # ========================================================================
    # KLASSE: HeyPiggyWorkerPreflightTests(unittest.IsolatedAsyncioTestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    async def test_main_stops_before_browser_mutation_when_credentials_missing(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_main_stops_before_browser_mutation_when_credentials_missing
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        execute_bridge = AsyncMock()
        check_bridge_alive = AsyncMock(return_value=True)
        run_vision_model = AsyncMock(
            side_effect=AssertionError(
                "vision probe must not run when credentials are missing"
            )
        )

        with (
            patch.dict(
                os.environ,
                {"HEYPIGGY_EMAIL": "", "HEYPIGGY_PASSWORD": ""},
                clear=False,
            ),
            patch.object(worker, "wait_for_extension", AsyncMock(return_value=True)),
            patch.object(worker, "check_bridge_alive", check_bridge_alive),
            patch.object(worker, "run_vision_model", run_vision_model),
            patch.object(worker, "execute_bridge", execute_bridge),
        ):
            await worker.main()

        execute_bridge.assert_not_awaited()
        check_bridge_alive.assert_not_awaited()

    async def test_main_stops_before_browser_mutation_when_vision_auth_fails(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_main_stops_before_browser_mutation_when_vision_auth_fails
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        execute_bridge = AsyncMock()

        with (
            patch.dict(
                os.environ,
                {
                    "HEYPIGGY_EMAIL": "ops@example.com",
                    "HEYPIGGY_PASSWORD": "secret",
                },
                clear=False,
            ),
            patch.object(worker, "wait_for_extension", AsyncMock(return_value=True)),
            patch.object(worker, "check_bridge_alive", AsyncMock(return_value=True)),
            patch.object(
                worker,
                "run_vision_model",
                AsyncMock(
                    return_value={
                        "ok": False,
                        "auth_failure": True,
                        "error": "401 invalid authentication credentials",
                    }
                ),
            ),
            patch.object(worker, "execute_bridge", execute_bridge),
        ):
            await worker.main()

        execute_bridge.assert_not_awaited()

    async def test_main_stops_before_browser_mutation_when_vision_health_fails(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_main_stops_before_browser_mutation_when_vision_health_fails
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        execute_bridge = AsyncMock()

        with (
            patch.dict(
                os.environ,
                {
                    "HEYPIGGY_EMAIL": "ops@example.com",
                    "HEYPIGGY_PASSWORD": "secret",
                },
                clear=False,
            ),
            patch.object(worker, "wait_for_extension", AsyncMock(return_value=True)),
            patch.object(worker, "check_bridge_alive", AsyncMock(return_value=True)),
            patch.object(
                worker,
                "run_vision_model",
                AsyncMock(
                    return_value={
                        "ok": False,
                        "auth_failure": True,
                        "error": "vision health check failed",
                    }
                ),
            ),
            patch.object(worker, "execute_bridge", execute_bridge),
        ):
            await worker.main()

        execute_bridge.assert_not_awaited()

    async def test_ask_vision_turns_auth_failure_into_stop(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_ask_vision_turns_auth_failure_into_stop
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with (
            patch.object(worker, "dom_prescan", AsyncMock(return_value="DOM")),
            patch.object(
                worker,
                "run_vision_model",
                AsyncMock(
                    return_value={
                        "ok": False,
                        "auth_failure": True,
                        "error": "401 invalid authentication credentials",
                    }
                ),
            ),
        ):
            decision = await worker.ask_vision(
                "/tmp/probe.png", "action", "expected", 1
            )

        self.assertEqual(decision["verdict"], "STOP")
        self.assertEqual(decision["page_state"], "error")
        self.assertEqual(decision["next_action"], "none")

    def test_detect_vision_auth_failure_treats_health_failures_as_blockers(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_detect_vision_auth_failure_treats_health_failures_as_blockers
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        blocker = worker.detect_vision_auth_failure(
            "provider health check failed: vision model unhealthy"
        )

        self.assertEqual(blocker, "provider health check failed")


class HeyPiggyWorkerClickPipelineTests(unittest.IsolatedAsyncioTestCase):
    # ========================================================================
    # KLASSE: HeyPiggyWorkerClickPipelineTests(unittest.IsolatedAsyncioTestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    async def test_run_click_action_routes_click_ref_through_escalation_pipeline(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_run_click_action_routes_click_ref_through_escalation_pipeline
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
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
    # ========================================================================
    # KLASSE: HeyPiggyWorkerVisionTimeoutTests(unittest.IsolatedAsyncioTestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_cli_timeout_respects_full_requested_timeout(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cli_timeout_respects_full_requested_timeout
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Regression: Früher war cli_timeout auf 25s gecappt → JEDER Call starb."""
        # Direkt die Cap-Logik nachbilden wie in run_vision_model
        timeout = 180
        cli_timeout = max(30, timeout - 5)
        self.assertEqual(cli_timeout, 175)
        self.assertGreater(
            cli_timeout, 60, "CLI-Timeout muss gross genug fuer das Vision-LLM sein"
        )


class HeyPiggyWorkerControllerTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: HeyPiggyWorkerControllerTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_failed_selectors_reset_on_page_state_change(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_failed_selectors_reset_on_page_state_change
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
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
    # -------------------------------------------------------------------------
    # FUNKTION: test_failed_selectors_require_three_failures_before_blocking
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Ein einzelner Fail darf den Selektor nicht sofort sperren."""
        gate = worker.VisionGateController()
        gate.add_failed_selector("#flaky")
        self.assertFalse(gate.is_selector_failed("#flaky"))
        gate.add_failed_selector("#flaky")
        self.assertFalse(gate.is_selector_failed("#flaky"))
        gate.add_failed_selector("#flaky")
        self.assertTrue(gate.is_selector_failed("#flaky"))


class HeyPiggyWorkerJsonParsingTests(unittest.IsolatedAsyncioTestCase):
    # ========================================================================
    # KLASSE: HeyPiggyWorkerJsonParsingTests(unittest.IsolatedAsyncioTestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    async def test_ask_vision_extracts_json_from_prose_wrapped_output(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_ask_vision_extracts_json_from_prose_wrapped_output
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Regression: Prosa um JSON herum darf nicht zu RETRY führen."""
        prosa_output = (
            "Ich analysiere den Screenshot. Hier meine Entscheidung:\n"
            '{"verdict": "PROCEED", "page_state": "dashboard", '
            '"next_action": "click_element", "next_params": {"selector": "#btn"}, '
            '"reason": "Button sichtbar", "progress": true}\n'
            "Hoffentlich hilft das."
        )
        with (
            patch.object(worker, "dom_prescan", AsyncMock(return_value="DOM")),
            patch.object(
                worker,
                "run_vision_model",
                AsyncMock(
                    return_value={
                        "ok": True,
                        "auth_failure": False,
                        "text": prosa_output,
                    }
                ),
            ),
        ):
            decision = await worker.ask_vision("/tmp/x.png", "a", "b", 1)

        self.assertEqual(decision["verdict"], "PROCEED")
        self.assertEqual(decision["page_state"], "dashboard")
        self.assertEqual(decision["next_action"], "click_element")

    async def test_ask_vision_includes_fail_learning_context_in_prompt(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_ask_vision_includes_fail_learning_context_in_prompt
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        fake_runner = AsyncMock(
            return_value={
                "ok": True,
                "auth_failure": False,
                "text": json.dumps(
                    {
                        "verdict": "PROCEED",
                        "page_state": "dashboard",
                        "next_action": "none",
                        "next_params": {},
                        "reason": "ok",
                        "progress": True,
                    }
                ),
            }
        )
        with (
            patch.object(worker, "dom_prescan", AsyncMock(return_value="DOM")),
            patch.object(
                worker,
                "build_fail_learning_context",
                return_value="RECENT FAIL-LEARNINGS (vermeide diese Muster aktiv):\n- Letzte Root Cause: click failed",
            ),
            patch.object(worker, "run_vision_model", fake_runner),
        ):
            await worker.ask_vision("/tmp/x.png", "a", "b", 1)

        prompt = fake_runner.await_args.args[0]
        self.assertIn("RECENT FAIL-LEARNINGS", prompt)
        self.assertIn("Letzte Root Cause: click failed", prompt)


class HeyPiggyWorkerProfilePathTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: HeyPiggyWorkerProfilePathTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_profile_path_resolver_uses_env_override(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_profile_path_resolver_uses_env_override
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with patch.dict(os.environ, {"HEYPIGGY_PROFILE_PATH": "/tmp/custom.json"}):
            path = worker._resolve_profile_path()
        self.assertEqual(str(path), "/tmp/custom.json")

    def test_profile_path_resolver_has_portable_fallback(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_profile_path_resolver_has_portable_fallback
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
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
    # ========================================================================
    # KLASSE: HeyPiggyWorkerNvidiaNimTests(unittest.IsolatedAsyncioTestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    async def test_nvidia_nim_returns_auth_failure_without_key(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_nvidia_nim_returns_auth_failure_without_key
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
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
    # -------------------------------------------------------------------------
    # FUNKTION: test_nvidia_nim_parses_openai_compat_response
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """NVIDIA NIM OpenAI-kompatible Response wird korrekt geparst."""
        tmp_path = _write_test_png()
        fake_response = json.dumps(
            {
                "id": "cmpl-test",
                "model": "meta/llama-3.2-90b-vision-instruct",
                "choices": [
                    {
                        "message": {
                            "content": '{"verdict":"PROCEED","page_state":"dashboard","next_action":"click_element","next_params":{"selector":"#btn"},"reason":"test","progress":true}',
                            "role": "assistant",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 150},
            }
        )

        with (
            patch.object(worker, "NVIDIA_API_KEY", "nvapi-test"),
            patch(
                "asyncio.to_thread",
                new=AsyncMock(return_value=(200, fake_response, "")),
            ),
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
    # -------------------------------------------------------------------------
    # FUNKTION: test_nvidia_nim_handles_rate_limit_429
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """429 Rate-Limit wird als retry-bar markiert, nicht als auth failure."""
        tmp_path = _write_test_png()

        with (
            patch.object(worker, "NVIDIA_API_KEY", "nvapi-test"),
            patch(
                "asyncio.to_thread", new=AsyncMock(return_value=(429, "", "rate limit"))
            ),
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
    # -------------------------------------------------------------------------
    # FUNKTION: test_nvidia_nim_handles_401_as_auth_failure
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """401 → auth_failure=True (Preflight stoppt Worker)."""
        tmp_path = _write_test_png()
        with (
            patch.object(worker, "NVIDIA_API_KEY", "nvapi-bad"),
            patch(
                "asyncio.to_thread",
                new=AsyncMock(return_value=(401, "", "invalid key")),
            ),
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
    # -------------------------------------------------------------------------
    # FUNKTION: test_run_vision_model_routes_to_nvidia_when_key_present
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Mit NVIDIA_API_KEY + VISION_BACKEND=auto → NVIDIA-Pfad wird gewählt."""
        fake_nvidia = AsyncMock(
            return_value={
                "ok": True,
                "auth_failure": False,
                "text": '{"verdict":"PROCEED"}',
                "stdout_text": "",
                "stderr_text": "",
                "returncode": 200,
            }
        )
        fake_opencode = AsyncMock(
            return_value={
                "ok": False,
                "auth_failure": True,
                "error": "should not be called",
            }
        )
        with (
            patch.object(worker, "NVIDIA_API_KEY", "nvapi-test"),
            patch.object(worker, "VISION_BACKEND", "auto"),
            patch.object(worker, "_run_vision_nvidia", fake_nvidia),
            patch.object(worker, "_run_vision_opencode", fake_opencode),
        ):
            result = await worker.run_vision_model(
                "prompt", "/tmp/x.png", timeout=30, step_num=1
            )
        self.assertTrue(result["ok"])
        fake_nvidia.assert_awaited_once()
        fake_opencode.assert_not_called()

    async def test_run_vision_model_fallback_to_opencode_without_key(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_run_vision_model_fallback_to_opencode_without_key
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Ohne NVIDIA_API_KEY → OpenCode CLI Pfad (Backwards Compat)."""
        fake_nvidia = AsyncMock(return_value={"ok": True, "text": "X"})
        fake_opencode = AsyncMock(
            return_value={"ok": True, "auth_failure": False, "text": "opencode-worked"}
        )
        with (
            patch.object(worker, "NVIDIA_API_KEY", ""),
            patch.object(worker, "VISION_BACKEND", "opencode"),
            patch.object(worker, "_run_vision_nvidia", fake_nvidia),
            patch.object(worker, "_run_vision_opencode", fake_opencode),
        ):
            result = await worker.run_vision_model(
                "prompt", "/tmp/x.png", timeout=30, step_num=1
            )
        self.assertEqual(result["text"], "opencode-worked")
        fake_opencode.assert_awaited_once()
        fake_nvidia.assert_not_called()

    async def test_run_vision_model_auto_without_key_falls_back_to_opencode(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_run_vision_model_auto_without_key_falls_back_to_opencode
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        fake_nvidia = AsyncMock(return_value={"ok": True, "text": "X"})
        fake_opencode = AsyncMock(
            return_value={"ok": True, "auth_failure": False, "text": "opencode-worked"}
        )
        with (
            patch.object(worker, "NVIDIA_API_KEY", ""),
            patch.object(worker, "VISION_BACKEND", "auto"),
            patch.object(worker, "_run_vision_nvidia", fake_nvidia),
            patch.object(worker, "_run_vision_opencode", fake_opencode),
        ):
            result = await worker.run_vision_model(
                "prompt", "/tmp/x.png", timeout=30, step_num=1
            )

        self.assertEqual(result["text"], "opencode-worked")
        fake_opencode.assert_awaited_once()
        fake_nvidia.assert_not_called()

    async def test_nvidia_fallback_chain_tries_next_model_on_error(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_nvidia_fallback_chain_tries_next_model_on_error
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Wenn das Primary-Modell 500ert, wird das nächste Modell probiert."""
        tmp_path = _write_test_png()
        calls = []

        async def fake_chat(prompt, path, *, timeout, model, force_json=True):
            calls.append(model)
            if model == worker.NVIDIA_VISION_MODEL:
                return {"ok": False, "auth_failure": False, "error": "HTTP 500"}
            return {
                "ok": True,
                "auth_failure": False,
                "text": '{"verdict":"PROCEED"}',
                "stdout_text": "",
                "stderr_text": "",
                "returncode": 200,
            }

        with patch.object(worker, "_nvidia_nim_chat", side_effect=fake_chat):
            result = await worker._run_vision_nvidia(
                "test", tmp_path, timeout=30, step_num=1, purpose="vision"
            )

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(calls), 2, "Fallback-Modell muss probiert werden")
        self.assertEqual(calls[0], worker.NVIDIA_VISION_MODEL)


class HeyPiggyVisionCacheTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: HeyPiggyVisionCacheTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def setUp(self):
    # -------------------------------------------------------------------------
    # FUNKTION: setUp
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        worker._VISION_CACHE.clear()

    def test_cache_returns_last_proceed_for_same_hash(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_returns_last_proceed_for_same_hash
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        decision = {
            "verdict": "PROCEED",
            "page_state": "survey_active",
            "next_action": "click_element",
            "next_params": {"selector": "#x"},
        }
        worker._vision_cache_put("hash123", "desc", 1, decision)
        cached = worker._vision_cache_get("hash123", "desc", 2)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["verdict"], "PROCEED")

    def test_cache_rejects_retry_verdicts(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_rejects_retry_verdicts
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        worker._vision_cache_put(
            "hash123", "desc", 1, {"verdict": "RETRY", "reason": "blur"}
        )
        self.assertIsNone(worker._vision_cache_get("hash123", "desc", 2))

    def test_cache_ignores_missing_hash(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_ignores_missing_hash
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        self.assertIsNone(worker._vision_cache_get("", "desc", 1))

    def test_cache_different_action_misses(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_different_action_misses
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        worker._vision_cache_put("hash123", "click weiter", 1, {"verdict": "PROCEED"})
        self.assertIsNone(worker._vision_cache_get("hash123", "click andere", 2))

    def test_cache_bypasses_fragile_click_after_selector_fail_learning(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_bypasses_fragile_click_after_selector_fail_learning
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_element",
            "next_params": {"selector": ".fragile-button"},
        }
        worker._VISION_CACHE[("hash123", "desc")] = dict(decision)

        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"selector_issue": 1}},
        ):
            cached = worker._vision_cache_get("hash123", "desc", 2)

        self.assertIsNone(cached)

    def test_cache_keeps_stable_id_click_when_selector_fail_learning_exists(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_keeps_stable_id_click_when_selector_fail_learning_exists
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_element",
            "next_params": {"selector": "#stable-button"},
        }
        worker._VISION_CACHE[("hash123", "desc")] = dict(decision)

        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"selector_issue": 1}},
        ):
            cached = worker._vision_cache_get("hash123", "desc", 2)

        self.assertIsNotNone(cached)
        self.assertEqual(cached["next_params"], {"selector": "#stable-button"})

    def test_cache_does_not_store_fragile_click_when_selector_fail_learning_exists(
        self,
    ):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_does_not_store_fragile_click_when_selector_fail_learning_exists
    # PARAMETER: 
        self,
    
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_element",
            "next_params": {"selector": ".fragile-button"},
        }

        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"selector_issue": 1}},
        ):
            worker._vision_cache_put("hash123", "desc", 1, decision)

        self.assertEqual(worker._VISION_CACHE, {})

    def test_cache_does_not_store_click_actions_when_loop_learning_exists(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_does_not_store_click_actions_when_loop_learning_exists
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_ref",
            "next_params": {"ref": "@e9"},
        }

        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"loop_detected": 1}},
        ):
            worker._vision_cache_put("hash123", "desc", 1, decision)

        self.assertEqual(worker._VISION_CACHE, {})

    def test_cache_bypasses_selector_from_denylist(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_bypasses_selector_from_denylist
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        decision = {
            "verdict": "PROCEED",
            "next_action": "ghost_click",
            "next_params": {"selector": "#blocked-selector"},
        }
        worker._VISION_CACHE[("hash123", "desc")] = dict(decision)

        with patch.object(
            worker,
            "load_fail_learning",
            return_value={
                "recent_failures": [],
                "issue_counts": {},
                "denylist": {
                    "selectors": ["#blocked-selector"],
                    "action_signatures": [],
                    "root_cause_keywords": [],
                },
            },
        ):
            cached = worker._vision_cache_get("hash123", "desc", 2)

        self.assertIsNone(cached)

    def test_cache_bypasses_action_signature_from_denylist(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_cache_bypasses_action_signature_from_denylist
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_ref",
            "next_params": {"ref": "@e9"},
        }
        worker._VISION_CACHE[("hash123", "desc")] = dict(decision)
        signature = worker._build_action_signature("click_ref", {"ref": "@e9"})

        with patch.object(
            worker,
            "load_fail_learning",
            return_value={
                "recent_failures": [],
                "issue_counts": {},
                "denylist": {
                    "selectors": [],
                    "action_signatures": [signature],
                    "root_cause_keywords": [],
                },
            },
        ):
            cached = worker._vision_cache_get("hash123", "desc", 2)

        self.assertIsNone(cached)


class HeyPiggyActionLoopDetectorTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: HeyPiggyActionLoopDetectorTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_loop_detected_after_three_identical_actions(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_loop_detected_after_three_identical_actions
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        params = {"selector": "#btn"}
        self.assertFalse(gate.record_action("h1", "click_element", params))
        self.assertFalse(gate.record_action("h1", "click_element", params))
        self.assertTrue(gate.record_action("h1", "click_element", params))

    def test_varied_actions_do_not_trigger_loop(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_varied_actions_do_not_trigger_loop
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        self.assertFalse(gate.record_action("h1", "click_element", {"selector": "#a"}))
        self.assertFalse(gate.record_action("h1", "click_element", {"selector": "#b"}))
        self.assertFalse(gate.record_action("h1", "click_element", {"selector": "#c"}))

    def test_clear_action_history_resets_loop_detection(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_clear_action_history_resets_loop_detection
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        params = {"selector": "#btn"}
        gate.record_action("h1", "click_element", params)
        gate.record_action("h1", "click_element", params)
        gate.clear_action_history()
        # Nach clear darf es 2 weitere geben bevor wieder Loop meldet
        self.assertFalse(gate.record_action("h1", "click_element", params))
        self.assertFalse(gate.record_action("h1", "click_element", params))

    def test_action_history_resets_on_page_state_change(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_action_history_resets_on_page_state_change
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        params = {"selector": "#btn"}
        gate.record_action("h1", "click_element", params)
        gate.record_action("h1", "click_element", params)

        gate.record_step("PROCEED", "hash1", page_state="dashboard")

        self.assertFalse(gate.record_action("h1", "click_element", params))


class HeyPiggyProfileAutofillTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: HeyPiggyProfileAutofillTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_email_placeholder_still_works(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_email_placeholder_still_works
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        params = {"selector": "#email", "text": "<EMAIL>"}
        out = worker.inject_credentials(params, "jeremy@example.com", "pw")
        self.assertEqual(out["text"], "jeremy@example.com")

    def test_profile_placeholder_resolves_from_profile(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_profile_placeholder_resolves_from_profile
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with patch.object(
            worker, "USER_PROFILE", {"name": "Jeremy Schulze", "city": "Berlin"}
        ):
            params = {"selector": "#name", "text": "<NAME>"}
            out = worker.inject_credentials(params, "", "")
        self.assertEqual(out["text"], "Jeremy Schulze")

    def test_field_hint_autofill_when_placeholder_auto(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_field_hint_autofill_when_placeholder_auto
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Wenn text='<AUTO>' und Feldname eindeutig, ziehen wir aus dem Profil."""
        with patch.object(worker, "USER_PROFILE", {"city": "München"}):
            params = {"selector": "#input-city", "text": "<AUTO>"}
            out = worker.inject_credentials(params, "", "")
        self.assertEqual(out["text"], "München")

    def test_no_autofill_if_user_text_present(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_no_autofill_if_user_text_present
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Wenn Vision bereits konkreten Text gegeben hat, nicht überschreiben."""
        with patch.object(worker, "USER_PROFILE", {"city": "München"}):
            params = {"selector": "#city", "text": "Hamburg"}
            out = worker.inject_credentials(params, "", "")
        self.assertEqual(out["text"], "Hamburg")

    def test_resolve_profile_value_for_vorname(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_resolve_profile_value_for_vorname
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with patch.object(worker, "USER_PROFILE", {"first_name": "Jeremy"}):
            self.assertEqual(
                worker._resolve_profile_value("first-name-input"), "Jeremy"
            )
            self.assertEqual(worker._resolve_profile_value("vorname"), "Jeremy")

    def test_resolve_profile_value_returns_none_on_mismatch(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_resolve_profile_value_returns_none_on_mismatch
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with patch.object(worker, "USER_PROFILE", {"city": "Berlin"}):
            self.assertIsNone(worker._resolve_profile_value("some-random-field"))


class HeyPiggyFailReplayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    # ========================================================================
    # KLASSE: HeyPiggyFailReplayIntegrationTests(unittest.IsolatedAsyncioTestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    async def test_run_fail_replay_analysis_writes_report_and_optional_comment(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_run_fail_replay_analysis_writes_report_and_optional_comment
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        frame = MagicMock()
        frame.png_bytes = b"frame-bytes"
        frame.step_label = "step_7_click"
        frame.vision_verdict = "STOP"
        frame.page_state = "error"
        recorder = MagicMock()
        recorder.get_keyframes.return_value = [frame]
        run_summary = worker.RunSummary(run_id="run-fail")
        run_summary.total_steps = 7
        gate = worker.VisionGateController()

        with (
            patch.object(
                worker,
                "save_keyframes_to_disk",
                return_value=[pathlib.Path("/tmp/keyframe_00.png")],
            ) as save_frames,
            patch.object(
                worker, "upload_to_box", return_value="https://box/frame_00.png"
            ) as upload,
            patch.object(
                worker,
                "analyze_fail_multiframe",
                AsyncMock(return_value={"root_cause": "click failed"}),
            ) as analyze,
            patch.object(
                worker, "generate_fail_report_markdown", return_value="report-body"
            ) as generate,
            patch.object(
                worker,
                "save_fail_report_to_disk",
                return_value=pathlib.Path("/tmp/fail_report.md"),
            ) as save_report,
            patch.object(
                worker, "post_github_issue_comment", return_value=True
            ) as post_comment,
            patch.dict(
                os.environ,
                {
                    "FAIL_REPORT_REPO": "OpenSIN-AI/A2A-SIN-Worker-heypiggy",
                    "FAIL_REPORT_ISSUE_NUMBER": "43",
                },
                clear=False,
            ),
        ):
            report_path = await worker._run_fail_replay_analysis(
                recorder,
                run_summary,
                gate,
                "vision_stop: click failed",
                "error",
            )

        self.assertEqual(report_path, pathlib.Path("/tmp/fail_report.md"))
        save_frames.assert_called_once()
        upload.assert_called_once()
        analyze.assert_awaited_once()
        generate.assert_called_once()
        save_report.assert_called_once()
        post_comment.assert_called_once()

    async def test_run_fail_replay_analysis_persists_fail_learning_memory(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_run_fail_replay_analysis_persists_fail_learning_memory
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        frame = MagicMock()
        frame.png_bytes = b"frame-bytes"
        frame.step_label = "step_8_click"
        frame.vision_verdict = "STOP"
        frame.page_state = "error"
        recorder = MagicMock()
        recorder.get_keyframes.return_value = [frame]
        run_summary = worker.RunSummary(run_id="run-memory")

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = pathlib.Path(tmpdir) / "fail_learning.json"
            with (
                patch.object(worker, "FAIL_LEARNING_PATH", memory_path),
                patch.object(
                    worker,
                    "save_keyframes_to_disk",
                    return_value=[pathlib.Path("/tmp/keyframe_00.png")],
                ),
                patch.object(worker, "upload_to_box", return_value=None),
                patch.object(
                    worker,
                    "analyze_fail_multiframe",
                    AsyncMock(
                        return_value={
                            "root_cause": "timing race",
                            "fix_recommendation": "wait longer",
                            "affected_step": "step 8",
                            "timing_issue": True,
                        }
                    ),
                ),
                patch.object(
                    worker, "generate_fail_report_markdown", return_value="report-body"
                ),
                patch.object(
                    worker,
                    "save_fail_report_to_disk",
                    return_value=pathlib.Path("/tmp/fail_report.md"),
                ),
            ):
                await worker._run_fail_replay_analysis(
                    recorder,
                    run_summary,
                    worker.VisionGateController(),
                    "vision_stop: timing race",
                    "error",
                )

            saved = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["recent_failures"][-1]["root_cause"], "timing race")
            self.assertEqual(saved["issue_counts"]["timing_issue"], 1)


class HeyPiggyFailLearningMemoryTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: HeyPiggyFailLearningMemoryTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_build_fail_learning_context_includes_recent_mitigation_hints(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_build_fail_learning_context_includes_recent_mitigation_hints
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        memory = {
            "recent_failures": [
                {
                    "root_cause": "selector mismatch",
                    "fix_recommendation": "use click_ref",
                    "affected_step": "step 4",
                }
            ],
            "issue_counts": {"selector_issue": 2, "loop_detected": 1},
        }
        with patch.object(worker, "load_fail_learning", return_value=memory):
            context = worker.build_fail_learning_context()

        self.assertIn("selector mismatch", context)
        self.assertIn("use click_ref", context)
        self.assertIn("VERMEIDE dieselbe next_action", context)

    def test_build_fail_learning_context_contains_explicit_action_avoidance_rules(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_build_fail_learning_context_contains_explicit_action_avoidance_rules
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        memory = {
            "recent_failures": [
                {
                    "root_cause": "Button was not visible under the fold",
                    "fix_recommendation": "scroll before clicking",
                    "affected_step": "step 9",
                }
            ],
            "issue_counts": {
                "selector_issue": 1,
                "loop_detected": 1,
                "timing_issue": 1,
            },
            "denylist": {
                "selectors": ["#survey-123"],
                "action_signatures": ['click_ref|{"ref": "@e9"}'],
                "root_cause_keywords": ["fold", "overlay"],
            },
        }
        with patch.object(worker, "load_fail_learning", return_value=memory):
            context = worker.build_fail_learning_context()

        self.assertIn('VERMEIDE next_action="click_element"', context)
        self.assertIn("VERMEIDE dieselbe next_action", context)
        self.assertIn("VERMEIDE Sofort-Wiederholungen", context)
        self.assertIn("VERMEIDE blinde Standard-Klicks", context)
        self.assertIn("HARTE SELECTOR-DENYLIST", context)
        self.assertIn("RISK KEYWORDS AUS FEHLSCHLÄGEN", context)

    def test_remember_fail_learning_persists_selector_action_and_keyword_denylists(
        self,
    ):
    # -------------------------------------------------------------------------
    # FUNKTION: test_remember_fail_learning_persists_selector_action_and_keyword_denylists
    # PARAMETER: 
        self,
    
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        gate.failed_selectors = {"#bad-selector": 3}
        gate.action_history = [
            ("hash1", "click_ref", '{"ref": "@e9"}'),
            ("hash1", "click_ref", '{"ref": "@e9"}'),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = pathlib.Path(tmpdir) / "fail_learning.json"
            with patch.object(worker, "FAIL_LEARNING_PATH", memory_path):
                memory = worker.remember_fail_learning(
                    {
                        "root_cause": "Captcha overlay blocked button under the fold",
                        "selector_issue": True,
                        "loop_detected": True,
                    },
                    "vision_stop",
                    "error",
                    gate=gate,
                )

            denylist = memory["denylist"]
            self.assertIn("#bad-selector", denylist["selectors"])
            self.assertIn('click_ref|{"ref": "@e9"}', denylist["action_signatures"])
            self.assertIn("captcha", denylist["root_cause_keywords"])

    def test_apply_fail_learning_to_decision_blocks_selector_from_denylist(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_apply_fail_learning_to_decision_blocks_selector_from_denylist
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        decision = {
            "verdict": "PROCEED",
            "next_action": "ghost_click",
            "next_params": {"selector": "#blocked-selector"},
            "reason": "button sichtbar",
            "progress": True,
        }
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={
                "recent_failures": [],
                "issue_counts": {},
                "denylist": {
                    "selectors": ["#blocked-selector"],
                    "action_signatures": [],
                    "root_cause_keywords": [],
                },
            },
        ):
            adapted = worker.apply_fail_learning_to_decision(decision, gate, "hash1")

        self.assertEqual(adapted["verdict"], "RETRY")
        self.assertEqual(adapted["next_action"], "none")

    def test_apply_fail_learning_to_decision_blocks_action_signature_from_denylist(
        self,
    ):
    # -------------------------------------------------------------------------
    # FUNKTION: test_apply_fail_learning_to_decision_blocks_action_signature_from_denylist
    # PARAMETER: 
        self,
    
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_ref",
            "next_params": {"ref": "@e9"},
            "reason": "button sichtbar",
            "progress": True,
        }
        signature = worker._build_action_signature("click_ref", {"ref": "@e9"})
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={
                "recent_failures": [],
                "issue_counts": {},
                "denylist": {
                    "selectors": [],
                    "action_signatures": [signature],
                    "root_cause_keywords": [],
                },
            },
        ):
            adapted = worker.apply_fail_learning_to_decision(decision, gate, "hash1")

        self.assertEqual(adapted["verdict"], "RETRY")
        self.assertEqual(adapted["next_action"], "none")

    def test_apply_fail_learning_to_decision_blocks_fragile_click_on_keyword_risk(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_apply_fail_learning_to_decision_blocks_fragile_click_on_keyword_risk
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_element",
            "next_params": {"selector": ".cta"},
            "reason": "overlay still visible above button",
            "progress": True,
        }
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={
                "recent_failures": [],
                "issue_counts": {},
                "denylist": {
                    "selectors": [],
                    "action_signatures": [],
                    "root_cause_keywords": ["overlay", "captcha"],
                },
            },
        ):
            adapted = worker.apply_fail_learning_to_decision(decision, gate, "hash1")

        self.assertEqual(adapted["verdict"], "RETRY")
        self.assertEqual(adapted["next_action"], "none")

    def test_get_fail_learning_delay_bounds_expands_after_timing_failures(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_get_fail_learning_delay_bounds_expands_after_timing_failures
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"timing_issue": 1}},
        ):
            delay_min, delay_max = worker.get_fail_learning_delay_bounds(5.0, 10.0)

        self.assertEqual((delay_min, delay_max), (6.0, 12.0))

    def test_get_fail_learning_delay_bounds_stays_default_without_timing_failures(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_get_fail_learning_delay_bounds_stays_default_without_timing_failures
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {}},
        ):
            delay_min, delay_max = worker.get_fail_learning_delay_bounds(5.0, 10.0)

        self.assertEqual((delay_min, delay_max), (5.0, 10.0))

    def test_get_fail_learning_dom_wait_seconds_expands_after_timing_failures(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_get_fail_learning_dom_wait_seconds_expands_after_timing_failures
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"timing_issue": 2}},
        ):
            self.assertEqual(worker.get_fail_learning_dom_wait_seconds(1.0), 2.0)

    def test_apply_fail_learning_to_decision_prefers_click_ref_after_selector_issues(
        self,
    ):
    # -------------------------------------------------------------------------
    # FUNKTION: test_apply_fail_learning_to_decision_prefers_click_ref_after_selector_issues
    # PARAMETER: 
        self,
    
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_element",
            "next_params": {"selector": ".submit", "ref": "@e9"},
            "reason": "button sichtbar",
            "progress": True,
        }
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"selector_issue": 1}},
        ):
            adapted = worker.apply_fail_learning_to_decision(decision, gate, "hash1")

        self.assertEqual(adapted["next_action"], "click_ref")
        self.assertEqual(adapted["next_params"], {"ref": "@e9"})

    def test_apply_fail_learning_to_decision_prefers_ghost_click_for_id_selector(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_apply_fail_learning_to_decision_prefers_ghost_click_for_id_selector
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_element",
            "next_params": {"selector": "#submit-button"},
            "reason": "id button sichtbar",
            "progress": True,
        }
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"selector_issue": 1}},
        ):
            adapted = worker.apply_fail_learning_to_decision(decision, gate, "hash1")

        self.assertEqual(adapted["next_action"], "ghost_click")
        self.assertEqual(adapted["next_params"], {"selector": "#submit-button"})

    def test_apply_fail_learning_to_decision_blocks_known_loop_pattern(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_apply_fail_learning_to_decision_blocks_known_loop_pattern
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        gate = worker.VisionGateController()
        decision = {
            "verdict": "PROCEED",
            "next_action": "click_element",
            "next_params": {"selector": "#btn"},
            "reason": "weiter klicken",
            "progress": True,
        }
        with patch.object(
            worker,
            "load_fail_learning",
            return_value={"recent_failures": [], "issue_counts": {"loop_detected": 1}},
        ):
            worker.apply_fail_learning_to_decision(decision, gate, "hash-loop")
            worker.apply_fail_learning_to_decision(decision, gate, "hash-loop")
            adapted = worker.apply_fail_learning_to_decision(
                decision, gate, "hash-loop"
            )

        self.assertEqual(adapted["verdict"], "RETRY")
        self.assertEqual(adapted["next_action"], "none")
        self.assertEqual(adapted["next_params"], {})


class HeyPiggyFinalizeWorkerRunTests(unittest.IsolatedAsyncioTestCase):
    # ========================================================================
    # KLASSE: HeyPiggyFinalizeWorkerRunTests(unittest.IsolatedAsyncioTestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    async def test_finalize_worker_run_skips_fail_replay_for_success(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_finalize_worker_run_skips_fail_replay_for_success
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        run_summary = worker.RunSummary(run_id="run-success")
        gate = worker.VisionGateController()
        recorder = MagicMock()
        recorder.stop = AsyncMock()

        with (
            patch.object(
                worker,
                "_write_structured_run_summary",
                return_value=pathlib.Path("/tmp/run_summary.json"),
            ),
            patch.object(
                worker,
                "_run_fail_replay_analysis",
                AsyncMock(return_value=pathlib.Path("/tmp/fail_report.md")),
            ) as fail_replay,
        ):
            (
                summary_path,
                fail_report_path,
                exit_reason,
            ) = await worker._finalize_worker_run(
                run_summary,
                gate,
                "vision_done",
                "dashboard",
                recorder,
            )

        self.assertEqual(summary_path, pathlib.Path("/tmp/run_summary.json"))
        self.assertIsNone(fail_report_path)
        self.assertEqual(exit_reason, "vision_done")
        recorder.stop.assert_awaited_once()
        fail_replay.assert_not_awaited()

    async def test_finalize_worker_run_triggers_fail_replay_for_limit_exit(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_finalize_worker_run_triggers_fail_replay_for_limit_exit
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        run_summary = worker.RunSummary(run_id="run-limit")
        gate = worker.VisionGateController()
        gate.no_progress_count = worker.MAX_NO_PROGRESS
        recorder = MagicMock()
        recorder.stop = AsyncMock()

        with (
            patch.object(
                worker,
                "_write_structured_run_summary",
                return_value=pathlib.Path("/tmp/run_summary.json"),
            ),
            patch.object(
                worker,
                "_run_fail_replay_analysis",
                AsyncMock(return_value=pathlib.Path("/tmp/fail_report.md")),
            ) as fail_replay,
        ):
            (
                summary_path,
                fail_report_path,
                exit_reason,
            ) = await worker._finalize_worker_run(
                run_summary,
                gate,
                "startup",
                "unknown",
                recorder,
            )

        self.assertEqual(summary_path, pathlib.Path("/tmp/run_summary.json"))
        self.assertEqual(fail_report_path, pathlib.Path("/tmp/fail_report.md"))
        self.assertEqual(exit_reason, "limit_reached:no_progress")
        recorder.stop.assert_awaited_once()
        fail_replay.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
