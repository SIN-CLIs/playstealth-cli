import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch


from config import (
    ArtifactConfig,
    BridgeConfig,
    VisionConfig,
    WorkerConfig,
    load_config_from_env,
)


class WorkerConfigTests(unittest.TestCase):
    def test_worker_config_uses_typed_nested_defaults(self):
        cfg = WorkerConfig()

        self.assertIsInstance(cfg.bridge, BridgeConfig)
        self.assertIsInstance(cfg.vision, VisionConfig)
        self.assertEqual(
            cfg.bridge.mcp_url, "https://openjerro-opensin-bridge-mcp.hf.space/mcp"
        )
        self.assertEqual(cfg.vision.model, "nvidia/meta/llama-3.2-11b-vision-instruct")
        self.assertEqual(
            cfg.nvidia.primary_model, "nvidia/meta/llama-3.2-11b-vision-instruct"
        )
        self.assertEqual(
            cfg.nvidia.fallback_models,
            (
                "nvidia/microsoft/phi-3.5-vision-instruct",
                "nvidia/microsoft/phi-3-vision-128k-instruct",
            ),
        )
        self.assertIn("click_element", cfg.click_actions)
        self.assertIn("vision_click", cfg.click_actions)

    def test_config_objects_are_frozen_against_runtime_mutation(self):
        cfg = WorkerConfig()

        with self.assertRaises(FrozenInstanceError):
            setattr(cfg.bridge, "mcp_url", "https://example.invalid/mcp")

        with self.assertRaises(FrozenInstanceError):
            setattr(cfg, "click_actions", ("click_element",))


class ArtifactConfigTests(unittest.TestCase):
    def test_artifact_paths_are_derived_from_run_id(self):
        cfg = ArtifactConfig(run_id="run-123", base_dir="/tmp/heypiggy")

        self.assertEqual(cfg.artifact_dir, Path("/tmp/heypiggy/heypiggy_run_run-123"))
        self.assertEqual(
            cfg.screenshot_dir,
            Path("/tmp/heypiggy/heypiggy_run_run-123/screenshots"),
        )
        self.assertEqual(
            cfg.audit_dir, Path("/tmp/heypiggy/heypiggy_run_run-123/audit")
        )
        self.assertEqual(
            cfg.session_dir,
            Path("/tmp/heypiggy/heypiggy_run_run-123/sessions"),
        )

    def test_ensure_dirs_creates_complete_directory_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = ArtifactConfig(run_id="run-456", base_dir=tmpdir)

            cfg.ensure_dirs()

            self.assertTrue(cfg.artifact_dir.is_dir())
            self.assertTrue(cfg.screenshot_dir.is_dir())
            self.assertTrue(cfg.audit_dir.is_dir())
            self.assertTrue(cfg.session_dir.is_dir())


class LoadConfigFromEnvTests(unittest.TestCase):
    def test_load_config_from_env_uses_defaults_when_env_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_config_from_env()

        self.assertEqual(
            cfg.bridge.health_url,
            "https://openjerro-opensin-bridge-mcp.hf.space/health",
        )
        self.assertEqual(cfg.vision.max_steps, 120)
        self.assertEqual(cfg.recorder.keyframes_on_fail, 12)
        self.assertEqual(cfg.artifacts.base_dir, "/tmp")
        self.assertTrue(cfg.artifacts.run_id)

    def test_load_config_from_env_applies_overrides_and_type_casts(self):
        with patch.dict(
            os.environ,
            {
                "BRIDGE_MCP_URL": "https://bridge.example/mcp",
                "BRIDGE_HEALTH_URL": "https://bridge.example/health",
                "BRIDGE_CONNECT_TIMEOUT": "42",
                "VISION_MODEL": "google/custom-model",
                "MAX_STEPS": "77",
                "MAX_RETRIES": "9",
                "MAX_NO_PROGRESS": "11",
                "MAX_CLICK_ESCALATIONS": "6",
                "VISION_CLI_TIMEOUT": "210",
                "NVIDIA_API_KEY": "nvapi-test",
                "NVIDIA_NIM_BASE_URL": "https://nim.example/v1",
                "NVIDIA_PRIMARY_MODEL": "meta/custom-vision",
                "NVIDIA_FALLBACK_MODELS": "nvidia/microsoft/phi-3.5-vision-instruct,nvidia/microsoft/phi-3-vision-128k-instruct",
                "NVIDIA_TIMEOUT": "88",
                "NVIDIA_MAX_INLINE_BYTES": "123456",
                "RECORDER_FPS": "2.5",
                "RECORDER_BUFFER_SECONDS": "33.5",
                "RECORDER_KEYFRAMES": "7",
                "HEYPIGGY_RUN_ID": "manual-run-id",
                "HEYPIGGY_ARTIFACT_BASE": "/var/tmp/heypiggy",
            },
            clear=True,
        ):
            cfg = load_config_from_env()

        self.assertEqual(cfg.bridge.mcp_url, "https://bridge.example/mcp")
        self.assertEqual(cfg.bridge.health_url, "https://bridge.example/health")
        self.assertEqual(cfg.bridge.connect_timeout, 42)
        self.assertEqual(cfg.vision.model, "google/custom-model")
        self.assertEqual(cfg.vision.max_steps, 77)
        self.assertEqual(cfg.vision.max_retries, 9)
        self.assertEqual(cfg.vision.max_no_progress, 11)
        self.assertEqual(cfg.vision.max_click_escalations, 6)
        self.assertEqual(cfg.vision.cli_timeout, 210)
        self.assertEqual(cfg.nvidia.api_key, "nvapi-test")
        self.assertEqual(cfg.nvidia.base_url, "https://nim.example/v1")
        self.assertEqual(cfg.nvidia.primary_model, "meta/custom-vision")
        self.assertEqual(
            cfg.nvidia.fallback_models,
            (
                "nvidia/microsoft/phi-3.5-vision-instruct",
                "nvidia/microsoft/phi-3-vision-128k-instruct",
            ),
        )
        self.assertEqual(cfg.nvidia.timeout, 88)
        self.assertEqual(cfg.nvidia.max_inline_bytes, 123456)
        self.assertEqual(cfg.recorder.fps, 2.5)
        self.assertEqual(cfg.recorder.buffer_seconds, 33.5)
        self.assertEqual(cfg.recorder.keyframes_on_fail, 7)
        self.assertEqual(cfg.artifacts.run_id, "manual-run-id")
        self.assertEqual(cfg.artifacts.base_dir, "/var/tmp/heypiggy")
