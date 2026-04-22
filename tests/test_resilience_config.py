"""Test suite for ResilienceConfig module."""
import os
import pytest
from unittest.mock import patch
from playstealth_actions.resilience_config import (
    ResilienceConfig,
    set_global_config,
    get_global_config
)


class TestResilienceConfigDefaults:
    """Tests for ResilienceConfig default values."""

    def test_default_retry_settings(self):
        """Test default retry configuration."""
        cfg = ResilienceConfig()
        
        assert cfg.max_retries == 3
        assert cfg.retry_delay_base == 1.0
        assert cfg.retry_delay_max == 10.0

    def test_default_timeout_settings(self):
        """Test default timeout configuration."""
        cfg = ResilienceConfig()
        
        assert cfg.navigation_timeout == 30000
        assert cfg.action_timeout == 15000
        assert cfg.request_timeout == 60000

    def test_default_fallback_settings(self):
        """Test default fallback configuration."""
        cfg = ResilienceConfig()
        
        assert cfg.enable_fallback_selectors is True
        assert cfg.enable_smart_resolver is True

    def test_default_circuit_breaker(self):
        """Test default circuit breaker configuration."""
        cfg = ResilienceConfig()
        
        assert cfg.circuit_breaker_threshold == 5
        assert cfg.circuit_breaker_reset_timeout == 60

    def test_default_reporting_settings(self):
        """Test default reporting configuration."""
        cfg = ResilienceConfig()
        
        assert cfg.enable_github_reporting is False
        assert cfg.enable_auto_heal is False

    def test_default_telemetry_settings(self):
        """Test default telemetry configuration."""
        cfg = ResilienceConfig()
        
        assert cfg.enable_telemetry is True
        assert cfg.telemetry_batch_size == 10


class TestResilienceConfigFromEnvOrArgs:
    """Tests for from_env_or_args class method."""

    def test_from_env_with_custom_values(self):
        """Test config creation with custom environment variables."""
        env_vars = {
            "PLAYSTEALTH_MAX_RETRIES": "5",
            "PLAYSTEALTH_NAV_TIMEOUT": "45000",
            "PLAYSTEALTH_ACTION_TIMEOUT": "20000",
            "GITHUB_APP_ID": "12345",
            "PLAYSTEALTH_AUTO_HEAL": "true",
            "PLAYSTEALTH_TELEMETRY": "false"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ResilienceConfig.from_env_or_args()
            
            assert cfg.max_retries == 5
            assert cfg.navigation_timeout == 45000
            assert cfg.action_timeout == 20000
            assert cfg.enable_github_reporting is True
            assert cfg.enable_auto_heal is True
            assert cfg.enable_telemetry is False

    def test_from_env_defaults_when_not_set(self):
        """Test config uses defaults when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = ResilienceConfig.from_env_or_args()
            
            assert cfg.max_retries == 3
            assert cfg.navigation_timeout == 30000
            assert cfg.action_timeout == 15000
            assert cfg.enable_github_reporting is False
            assert cfg.enable_auto_heal is False
            assert cfg.enable_telemetry is True

    def test_from_env_auto_heal_variations(self):
        """Test auto_heal parsing with different boolean representations."""
        # Test "true" (case variations)
        for true_val in ["true", "True", "TRUE"]:
            with patch.dict(os.environ, {"PLAYSTEALTH_AUTO_HEAL": true_val}, clear=True):
                cfg = ResilienceConfig.from_env_or_args()
                assert cfg.enable_auto_heal is True
        
        # Test "false" (case variations)
        for false_val in ["false", "False", "FALSE"]:
            with patch.dict(os.environ, {"PLAYSTEALTH_AUTO_HEAL": false_val}, clear=True):
                cfg = ResilienceConfig.from_env_or_args()
                assert cfg.enable_auto_heal is False
        
        # Test empty/missing
        with patch.dict(os.environ, {}, clear=True):
            cfg = ResilienceConfig.from_env_or_args()
            assert cfg.enable_auto_heal is False

    def test_from_env_telemetry_variations(self):
        """Test telemetry parsing with different boolean representations."""
        # Test "false" disables telemetry
        for false_val in ["false", "False", "FALSE"]:
            with patch.dict(os.environ, {"PLAYSTEALTH_TELEMETRY": false_val}, clear=True):
                cfg = ResilienceConfig.from_env_or_args()
                assert cfg.enable_telemetry is False
        
        # Test "true" and default enable telemetry
        for true_val in ["true", "True", "TRUE", ""]:
            with patch.dict(os.environ, {"PLAYSTEALTH_TELEMETRY": true_val}, clear=True):
                cfg = ResilienceConfig.from_env_or_args()
                assert cfg.enable_telemetry is True

    def test_from_args_override(self):
        """Test that CLI args can override defaults."""
        class MockArgs:
            max_retries = 7
        
        with patch.dict(os.environ, {}, clear=True):
            cfg = ResilienceConfig.from_env_or_args(args=MockArgs())
            
            assert cfg.max_retries == 7

    def test_from_env_overrides_args(self):
        """Test that environment variables take precedence over args."""
        class MockArgs:
            max_retries = 7
        
        env_vars = {
            "PLAYSTEALTH_MAX_RETRIES": "10"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ResilienceConfig.from_env_or_args(args=MockArgs())
            
            # Env var should win
            assert cfg.max_retries == 10

    def test_from_args_none_uses_default(self):
        """Test that None args use default values."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = ResilienceConfig.from_env_or_args(args=None)
            
            assert cfg.max_retries == 3


class TestGlobalConfig:
    """Tests for global config functions."""

    def setup_method(self):
        """Reset global config before each test."""
        import playstealth_actions.resilience_config as rc
        rc._global_config = None

    def test_set_and_get_global_config(self):
        """Test setting and getting global config."""
        cfg = ResilienceConfig(max_retries=5, navigation_timeout=50000)
        set_global_config(cfg)
        
        retrieved = get_global_config()
        
        assert retrieved is cfg
        assert retrieved.max_retries == 5
        assert retrieved.navigation_timeout == 50000

    def test_get_global_config_creates_default(self):
        """Test that get_global_config creates default if not set."""
        # Ensure no global config is set
        import playstealth_actions.resilience_config as rc
        rc._global_config = None
        
        cfg = get_global_config()
        
        assert cfg is not None
        assert isinstance(cfg, ResilienceConfig)
        assert cfg.max_retries == 3  # default
        
        # Should be cached now
        same_cfg = get_global_config()
        assert same_cfg is cfg

    def test_set_global_config_overwrites(self):
        """Test that setting global config overwrites previous value."""
        cfg1 = ResilienceConfig(max_retries=3)
        cfg2 = ResilienceConfig(max_retries=10)
        
        set_global_config(cfg1)
        assert get_global_config().max_retries == 3
        
        set_global_config(cfg2)
        assert get_global_config().max_retries == 10

    def test_global_config_thread_safety_basic(self):
        """Basic test that global config is accessible."""
        cfg = ResilienceConfig()
        set_global_config(cfg)
        
        # Multiple gets should return same instance
        cfg1 = get_global_config()
        cfg2 = get_global_config()
        cfg3 = get_global_config()
        
        assert cfg1 is cfg2 is cfg3


class TestResilienceConfigDataclass:
    """Tests for ResilienceConfig as a dataclass."""

    def test_dataclass_fields(self):
        """Test that all expected fields exist."""
        cfg = ResilienceConfig()
        
        # Check all fields are accessible
        assert hasattr(cfg, 'max_retries')
        assert hasattr(cfg, 'retry_delay_base')
        assert hasattr(cfg, 'retry_delay_max')
        assert hasattr(cfg, 'navigation_timeout')
        assert hasattr(cfg, 'action_timeout')
        assert hasattr(cfg, 'request_timeout')
        assert hasattr(cfg, 'enable_fallback_selectors')
        assert hasattr(cfg, 'enable_smart_resolver')
        assert hasattr(cfg, 'circuit_breaker_threshold')
        assert hasattr(cfg, 'circuit_breaker_reset_timeout')
        assert hasattr(cfg, 'enable_github_reporting')
        assert hasattr(cfg, 'enable_auto_heal')
        assert hasattr(cfg, 'enable_telemetry')
        assert hasattr(cfg, 'telemetry_batch_size')

    def test_dataclass_equality(self):
        """Test dataclass equality comparison."""
        cfg1 = ResilienceConfig(max_retries=5)
        cfg2 = ResilienceConfig(max_retries=5)
        cfg3 = ResilienceConfig(max_retries=10)
        
        assert cfg1 == cfg2
        assert cfg1 != cfg3

    def test_dataclass_repr(self):
        """Test dataclass string representation."""
        cfg = ResilienceConfig(max_retries=5)
        repr_str = repr(cfg)
        
        assert "ResilienceConfig" in repr_str
        assert "max_retries=5" in repr_str

    def test_dataclass_immutable_fields_not_enforced(self):
        """Test that dataclass fields can be modified (not frozen)."""
        cfg = ResilienceConfig(max_retries=3)
        cfg.max_retries = 10
        
        assert cfg.max_retries == 10


class TestResilienceConfigIntegration:
    """Integration tests for ResilienceConfig usage patterns."""

    def setup_method(self):
        """Reset global config before each test."""
        import playstealth_actions.resilience_config as rc
        rc._global_config = None

    def test_full_config_workflow(self):
        """Test complete config workflow from env to global."""
        env_vars = {
            "PLAYSTEALTH_MAX_RETRIES": "7",
            "PLAYSTEALTH_NAV_TIMEOUT": "60000",
            "PLAYSTEALTH_AUTO_HEAL": "true",
            "GITHUB_APP_ID": "app123"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            # Create config from env
            cfg = ResilienceConfig.from_env_or_args()
            
            # Verify values
            assert cfg.max_retries == 7
            assert cfg.navigation_timeout == 60000
            assert cfg.enable_auto_heal is True
            assert cfg.enable_github_reporting is True
            
            # Set as global
            set_global_config(cfg)
            
            # Retrieve and verify
            global_cfg = get_global_config()
            assert global_cfg.max_retries == 7
            assert global_cfg.enable_auto_heal is True

    def test_config_with_cli_and_env_mix(self):
        """Test config with both CLI args and env vars."""
        class MockArgs:
            max_retries = 5  # CLI arg
        
        env_vars = {
            "PLAYSTEALTH_NAV_TIMEOUT": "45000",  # Env var
            "PLAYSTEALTH_AUTO_HEAL": "true"  # Env var
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ResilienceConfig.from_env_or_args(args=MockArgs())
            
            # CLI arg should be used (no env override for max_retries)
            assert cfg.max_retries == 5
            # Env vars should be used
            assert cfg.navigation_timeout == 45000
            assert cfg.enable_auto_heal is True
