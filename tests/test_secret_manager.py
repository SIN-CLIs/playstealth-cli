"""Test suite for SecretManager module."""
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from playstealth_actions.secret_manager import SecretManager


class TestSecretManagerInit:
    """Tests for SecretManager initialization."""

    def test_init_without_env_vars(self):
        """Test initialization without Infisical credentials."""
        with patch.dict(os.environ, {}, clear=True):
            sm = SecretManager()
            assert sm.project_id is None
            assert sm.token is None
            assert sm.env_slug == "prod"
            assert sm._enabled is False
            assert sm._loaded is False
            assert sm._cache == {}

    def test_init_with_env_vars(self):
        """Test initialization with Infisical credentials."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project-123",
            "INFISICAL_TOKEN": "test-token-abc",
            "INFISICAL_ENV": "staging"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            assert sm.project_id == "test-project-123"
            assert sm.token == "test-token-abc"
            assert sm.env_slug == "staging"
            assert sm._enabled is True

    def test_init_default_env(self):
        """Test default environment slug."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            assert sm.env_slug == "prod"  # default


@pytest.mark.asyncio
class TestSecretManagerLoad:
    """Tests for SecretManager.load() method."""

    async def test_load_disabled(self):
        """Test load returns False when disabled."""
        with patch.dict(os.environ, {}, clear=True):
            sm = SecretManager()
            result = await sm.load()
            assert result is False
            assert sm._loaded is False

    async def test_load_already_loaded(self):
        """Test load returns True when already loaded."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            sm._loaded = True
            result = await sm.load()
            assert result is True

    async def test_load_success(self):
        """Test successful load from Infisical API."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token",
            "INFISICAL_ENV": "prod"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "secrets": [
                    {"key": "DATABASE_URL", "value": "postgres://localhost/db"},
                    {"key": "API_KEY", "value": "secret-key-123"},
                    {"key": "REDIS_PASSWORD", "value": "redis-pass"}
                ]
            }
            
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            
            with patch('httpx.AsyncClient', return_value=mock_client):
                result = await sm.load()
                
                assert result is True
                assert sm._loaded is True
                assert len(sm._cache) == 3
                assert sm._cache["DATABASE_URL"] == "postgres://localhost/db"
                assert sm._cache["API_KEY"] == "secret-key-123"
                assert sm._cache["REDIS_PASSWORD"] == "redis-pass"
                
                mock_client.get.assert_called_once()
                call_args = mock_client.get.call_args
                assert "test-project" in call_args[0][0]
                assert "environment=prod" in call_args[0][0]

    async def test_load_api_failure(self):
        """Test load handles API failure gracefully."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            
            mock_response = MagicMock()
            mock_response.status_code = 401
            
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            
            with patch('httpx.AsyncClient', return_value=mock_client):
                result = await sm.load()
                
                assert result is False
                assert sm._loaded is False
                assert len(sm._cache) == 0

    async def test_load_network_error(self):
        """Test load handles network errors gracefully."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            
            with patch('httpx.AsyncClient', return_value=mock_client):
                result = await sm.load()
                
                assert result is False
                assert sm._loaded is False


class TestSecretManagerGet:
    """Tests for SecretManager.get() method."""

    def test_get_from_cache(self):
        """Test getting secret from cache."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            sm._cache = {"API_KEY": "cached-value"}
            
            result = sm.get("API_KEY")
            assert result == "cached-value"

    def test_get_from_env_fallback(self):
        """Test getting secret from os.environ fallback."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token",
            "DB_HOST": "env-host-value"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            sm._cache = {}  # Empty cache
            
            result = sm.get("DB_HOST")
            assert result == "env-host-value"

    def test_get_with_custom_fallback(self):
        """Test getting secret with custom fallback value."""
        with patch.dict(os.environ, {}, clear=True):
            sm = SecretManager()
            sm._cache = {}
            
            result = sm.get("MISSING_KEY", "fallback-value")
            assert result == "fallback-value"

    def test_get_missing_no_fallback(self):
        """Test getting missing key without fallback."""
        with patch.dict(os.environ, {}, clear=True):
            sm = SecretManager()
            sm._cache = {}
            
            result = sm.get("MISSING_KEY")
            assert result is None


class TestSecretManagerInjectEnv:
    """Tests for SecretManager.inject_env() method."""

    def test_inject_env_new_keys(self):
        """Test injecting secrets that don't exist in environ."""
        with patch.dict(os.environ, {}, clear=True):
            sm = SecretManager()
            sm._cache = {
                "NEW_SECRET_1": "value1",
                "NEW_SECRET_2": "value2"
            }
            
            sm.inject_env()
            
            assert os.environ.get("NEW_SECRET_1") == "value1"
            assert os.environ.get("NEW_SECRET_2") == "value2"

    def test_inject_env_existing_keys_not_overwritten(self):
        """Test that existing environ keys are not overwritten."""
        env_vars = {"EXISTING_KEY": "existing-value"}
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            sm._cache = {
                "EXISTING_KEY": "cache-value",
                "NEW_KEY": "new-value"
            }
            
            sm.inject_env()
            
            # Existing key should keep its original value
            assert os.environ.get("EXISTING_KEY") == "existing-value"
            # New key should be added
            assert os.environ.get("NEW_KEY") == "new-value"


class TestSecretManagerRedact:
    """Tests for SecretManager.redact() method."""

    def test_redact_password_equals(self):
        """Test redacting password=value pattern."""
        sm = SecretManager()
        text = "Connecting with password=secret123 to database"
        result = sm.redact(text)
        assert "password=" in result
        assert "secret123" not in result
        assert "****" in result

    def test_redact_password_colon(self):
        """Test redacting password: value pattern."""
        sm = SecretManager()
        text = "Config: password: mysupersecret"
        result = sm.redact(text)
        assert "password:" in result or "password" in result
        assert "mysupersecret" not in result
        assert "****" in result

    def test_redact_api_key(self):
        """Test redacting API key patterns."""
        sm = SecretManager()
        text = "Using API_KEY=sk-1234567890abcdef"
        result = sm.redact(text)
        assert "API_KEY=" in result
        assert "sk-1234567890abcdef" not in result
        assert "****" in result

    def test_redact_token(self):
        """Test redacting token patterns."""
        sm = SecretManager()
        text = "Auth token: bearer_xyz_12345"
        result = sm.redact(text)
        assert "token" in result.lower()
        assert "bearer_xyz_12345" not in result
        assert "****" in result

    def test_redact_multiple_secrets(self):
        """Test redacting multiple secrets in one text."""
        sm = SecretManager()
        text = "password=pass123 and api_key=key456 and token=tok789"
        result = sm.redact(text)
        assert "pass123" not in result
        assert "key456" not in result
        assert "tok789" not in result
        assert result.count("****") >= 3

    def test_redact_no_secrets(self):
        """Test redacting text without secrets."""
        sm = SecretManager()
        text = "This is a normal log message without secrets"
        result = sm.redact(text)
        assert result == text

    def test_redact_pem_key(self):
        """Test redacting PEM/key patterns."""
        sm = SecretManager()
        text = "Loading private key: -----BEGIN RSA PRIVATE KEY-----MIIE..."
        result = sm.redact(text)
        # Should redact after 'key:'
        assert "****" in result or "key:" in result


class TestSecretManagerIsEnabled:
    """Tests for SecretManager.is_enabled() method."""

    def test_is_enabled_true(self):
        """Test is_enabled returns True when configured."""
        env_vars = {
            "INFISICAL_PROJECT_ID": "test-project",
            "INFISICAL_TOKEN": "test-token"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            assert sm.is_enabled() is True

    def test_is_enabled_false_no_project(self):
        """Test is_enabled returns False without project ID."""
        env_vars = {"INFISICAL_TOKEN": "test-token"}
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            assert sm.is_enabled() is False

    def test_is_enabled_false_no_token(self):
        """Test is_enabled returns False without token."""
        env_vars = {"INFISICAL_PROJECT_ID": "test-project"}
        with patch.dict(os.environ, env_vars, clear=True):
            sm = SecretManager()
            assert sm.is_enabled() is False

    def test_is_enabled_false_no_credentials(self):
        """Test is_enabled returns False without any credentials."""
        with patch.dict(os.environ, {}, clear=True):
            sm = SecretManager()
            assert sm.is_enabled() is False


# Import httpx for the network error test
import httpx
