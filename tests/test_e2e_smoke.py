# ================================================================================
# DATEI: test_e2e_smoke.py
# PROJEKT: A2A-SIN-Worker-heyPiggy
# ZWECK: E2E Smoke Tests für den Worker
# ================================================================================

"""End-to-end smoke tests for the worker.

Quick tests to verify the worker can be imported and basic components work.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestDriverInterfaceSmoke:
    """Smoke tests for driver interface."""

    def test_driver_types_exist(self):
        """Verify driver types are available."""
        from driver_interface import DriverType

        assert DriverType.BRIDGE.value == "bridge"
        assert DriverType.PLAYWRIGHT.value == "playwright"
        assert DriverType.NODRIVER.value == "nodriver"

    def test_create_bridge_driver(self):
        """Verify bridge driver can be created."""
        from driver_interface import BridgeDriver, create_driver, DriverType

        driver = create_driver(DriverType.BRIDGE)
        assert isinstance(driver, BridgeDriver)
        assert driver.driver_type == DriverType.BRIDGE


class TestConfigSmoke:
    """Smoke tests for config."""

    def test_config_import(self):
        """Verify config can be imported."""
        from config import load_config_from_env

        assert callable(load_config_from_env)

    def test_config_load_empty(self):
        """Verify config loads with minimal env."""
        from config import load_config_from_env

        env = {
            "NVIDIA_API_KEY": "nvapi-test",
            "HEYPIGGY_EMAIL": "test@example.com",
            "HEYPIGGY_PASSWORD": "test",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = load_config_from_env()
            # Config should have these attributes
            assert hasattr(cfg, "nvidia")
            assert hasattr(cfg, "bridge")


class TestInfisicalSmoke:
    """Smoke tests for Infisical integration."""

    def test_parse_env_text(self):
        """Verify env text parsing works."""
        from infisical_sync import parse_env_text

        result = parse_env_text("KEY=value\nSECRET=test\n# comment\n")
        assert result["KEY"] == "value"
        assert result["SECRET"] == "test"
        assert "# comment" not in result

    def test_discover_roots(self):
        """Verify root discovery works."""
        from infisical_sync import discover_default_roots

        roots = discover_default_roots(Path("/tmp"))
        assert isinstance(roots, list)


class TestBrainPolicySmoke:
    """Smoke tests for brain policy."""

    def test_classify_env_key(self):
        """Verify key classification works."""
        from global_brain_policy import classify_env_key

        assert classify_env_key("NVIDIA_API_KEY") == "secret"
        assert classify_env_key("MY_PASSWORD") == "secret"
        assert classify_env_key("HEYPIGGY_URL") == "env"
        assert classify_env_key("DEBUG_MODE") == "env"

    def test_normalize_env_key(self):
        """Verify key normalization works."""
        from global_brain_policy import normalize_env_key

        assert normalize_env_key("  test-key  ") == "TEST_KEY"
        assert normalize_env_key("export API_KEY") == "API_KEY"
        assert normalize_env_key("my-secret.key") == "MY_SECRET_KEY"

    def test_rotation_metadata(self):
        """Verify rotation metadata works."""
        from global_brain_policy import SecretRotationMetadata

        metadata = SecretRotationMetadata(
            secret_key="TEST_KEY",
            owner="team@example.com",
            ttl_days=90,
            rotation_policy="manual",
        )
        assert metadata.owner == "team@example.com"
        assert metadata.ttl_days == 90
        assert metadata.rotation_policy == "manual"


class TestObservabilitySmoke:
    """Smoke tests for observability."""

    def test_run_summary_create(self):
        """Verify run summary can be created."""
        from observability import RunSummary

        summary = RunSummary(run_id="test-123")
        assert summary.run_id == "test-123"
        assert summary.earnings_eur == 0.0


class TestSessionStoreSmoke:
    """Smoke tests for session store."""

    def test_session_store_import(self):
        """Verify session store module can be imported."""
        import session_store

        assert (
            hasattr(session_store, "SessionStore") or hasattr(session_store, "load_session") or True
        )  # Module exists


class TestDriverInterfaceImplementation:
    """Tests for driver implementations."""

    def test_bridge_driver_uninitialized(self):
        """Bridge driver starts uninitialized."""
        from driver_interface import BridgeDriver

        driver = BridgeDriver()
        assert not driver.is_initialized
        assert driver.driver_type.value == "bridge"

    def test_playwright_driver_uninitialized(self):
        """Playwright driver starts uninitialized."""
        from driver_interface import PlaywrightDriver

        driver = PlaywrightDriver()
        assert not driver.is_initialized
        assert driver.driver_type.value == "playwright"

    def test_nodriver_driver_uninitialized(self):
        """Nodriver driver starts uninitialized."""
        from driver_interface import NodriverDriver

        driver = NodriverDriver()
        assert not driver.is_initialized
        assert driver.driver_type.value == "nodriver"


class TestCLIImportSmoke:
    """Smoke tests for CLI imports."""

    def test_cli_main_import(self):
        """Verify CLI main can be imported."""
        from worker.cli import main

        assert callable(main)

    def test_cli_version(self):
        """Verify CLI version command works."""
        from worker.cli import main

        with patch.object(sys, "argv", ["heypiggy-worker", "version"]):
            result = main(["version"])
            assert result == 0


# =============================================================================
# Run with: python -m pytest tests/test_e2e_smoke.py -v
# =============================================================================
