"""Test suite for GitHubIssueReporter module."""
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from playstealth_actions.github_issue_reporter import GitHubIssueReporter


@pytest.fixture
def reporter():
    """Create GitHubIssueReporter instance with test config."""
    env_vars = {
        "GITHUB_APP_ID": "12345",
        "GITHUB_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\ntest-key\n-----END RSA PRIVATE KEY-----",
        "GITHUB_INSTALLATION_ID": "67890",
        "GITHUB_REPO_OWNER": "test-org",
        "GITHUB_REPO_NAME": "test-repo"
    }
    with patch.dict(os.environ, env_vars, clear=True):
        return GitHubIssueReporter()


class TestGitHubIssueReporterInit:
    """Tests for GitHubIssueReporter initialization."""

    def test_init_with_env_vars(self):
        """Test initialization with GitHub App credentials."""
        env_vars = {
            "GITHUB_APP_ID": "12345",
            "GITHUB_APP_PRIVATE_KEY": "test-private-key",
            "GITHUB_INSTALLATION_ID": "67890",
            "GITHUB_REPO_OWNER": "custom-org",
            "GITHUB_REPO_NAME": "custom-repo"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            reporter = GitHubIssueReporter()
            
            assert reporter._enabled is True
            assert reporter.app_id == "12345"
            assert reporter._private_key == "test-private-key"
            assert reporter.installation_id == "67890"
            assert reporter.repo_owner == "custom-org"
            assert reporter.repo_name == "custom-repo"
            assert reporter._issue_hashes == set()
            assert reporter._token_cache is None

    def test_init_without_credentials(self):
        """Test initialization without GitHub App credentials."""
        with patch.dict(os.environ, {}, clear=True):
            reporter = GitHubIssueReporter()
            
            assert reporter._enabled is False
            assert reporter.app_id is None
            assert reporter._private_key is None
            assert reporter.installation_id is None
            assert reporter.repo_owner == "your-org"  # default
            assert reporter.repo_name == "playstealth"  # default

    def test_init_default_repo_values(self):
        """Test default repository values when only partial env vars set."""
        env_vars = {
            "GITHUB_APP_ID": "123",
            "GITHUB_APP_PRIVATE_KEY": "key",
            "GITHUB_INSTALLATION_ID": "456"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            reporter = GitHubIssueReporter()
            
            assert reporter._enabled is True
            assert reporter.repo_owner == "your-org"
            assert reporter.repo_name == "playstealth"


class TestDedupHash:
    """Tests for _dedup_hash method."""

    def test_dedup_hash_consistent(self, reporter):
        """Test that same input produces same hash."""
        h1 = reporter._dedup_hash("module.name", "error message")
        h2 = reporter._dedup_hash("module.name", "error message")
        
        assert h1 == h2
        assert len(h1) == 12  # SHA256 first 12 chars

    def test_dedup_hash_different_modules(self, reporter):
        """Test that different modules produce different hashes."""
        h1 = reporter._dedup_hash("module.one", "same error")
        h2 = reporter._dedup_hash("module.two", "same error")
        
        assert h1 != h2

    def test_dedup_hash_different_errors(self, reporter):
        """Test that different errors produce different hashes."""
        h1 = reporter._dedup_hash("same.module", "error one")
        h2 = reporter._dedup_hash("same.module", "error two")
        
        assert h1 != h2

    def test_dedup_hash_case_sensitive(self, reporter):
        """Test that hash is case sensitive."""
        h1 = reporter._dedup_hash("Module.Name", "Error Message")
        h2 = reporter._dedup_hash("module.name", "error message")
        
        assert h1 != h2


@pytest.mark.asyncio
class TestGetInstallationToken:
    """Tests for _get_installation_token method."""

    async def test_get_token_cached(self, reporter):
        """Test that cached token is returned if not expired."""
        reporter._token_cache = "cached-token-123"
        reporter._token_expires = datetime.now(timezone.utc).replace(year=2099)
        
        result = await reporter._get_installation_token()
        
        assert result == "cached-token-123"

    async def test_get_token_success(self, reporter):
        """Test successful token retrieval from GitHub API."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "token": "new-installation-token-xyz",
            "expires_at": "2024-12-31T23:59:59Z"
        }
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        # Fix: Set a valid future expiration time
        from datetime import timedelta
        future_time = datetime.now(timezone.utc) + timedelta(minutes=8)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            with patch('jwt.encode', return_value="fake-jwt-token"):
                result = await reporter._get_installation_token()
                
                assert result == "new-installation-token-xyz"
                assert reporter._token_cache == "new-installation-token-xyz"
                assert reporter._token_expires is not None
                
                mock_client.post.assert_called_once()
                call_args = mock_client.post.call_args
                assert "installations/67890/access_tokens" in call_args[0][0]

    async def test_get_token_api_failure(self, reporter):
        """Test token retrieval handles API failure gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            with patch('jwt.encode', return_value="fake-jwt-token"):
                result = await reporter._get_installation_token()
                
                assert result is None

    async def test_get_token_exception_handling(self, reporter):
        """Test token retrieval handles exceptions gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            with patch('jwt.encode', return_value="fake-jwt-token"):
                result = await reporter._get_installation_token()
                
                assert result is None


@pytest.mark.asyncio
class TestCreateIssue:
    """Tests for create_issue method."""

    async def test_create_issue_disabled(self):
        """Test issue creation when reporter is disabled."""
        with patch.dict(os.environ, {}, clear=True):
            reporter = GitHubIssueReporter()
            
            result = await reporter.create_issue(
                module_name="test.module",
                error_msg="Test error",
                traceback_str="Test traceback"
            )
            
            assert result is None

    async def test_create_issue_deduplication(self, reporter):
        """Test that duplicate issues are prevented."""
        module_name = "test.module"
        error_msg = "Duplicate error"
        
        # Add hash to already-reported set
        h = reporter._dedup_hash(module_name, error_msg)
        reporter._issue_hashes.add(h)
        
        result = await reporter.create_issue(
            module_name=module_name,
            error_msg=error_msg,
            traceback_str="Test traceback"
        )
        
        assert result is None

    async def test_create_issue_no_token(self, reporter):
        """Test issue creation fails gracefully without token."""
        reporter._get_installation_token = AsyncMock(return_value=None)
        
        result = await reporter.create_issue(
            module_name="test.module",
            error_msg="Test error",
            traceback_str="Test traceback"
        )
        
        assert result is None

    async def test_create_issue_success(self, reporter):
        """Test successful issue creation."""
        # Mock token retrieval
        reporter._get_installation_token = AsyncMock(return_value="valid-token")
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/test-org/test-repo/issues/123"
        }
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await reporter.create_issue(
                module_name="test.module",
                error_msg="Test error message",
                traceback_str="Test traceback details",
                severity="high",
                labels=["bug", "auto-generated"]
            )
            
            assert result == "https://github.com/test-org/test-repo/issues/123"
            
            # Verify issue was added to dedup set
            assert len(reporter._issue_hashes) == 1
            
            # Verify API call
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            
            assert "🚨 HIGH:" in payload["title"]
            assert "test.module" in payload["title"]
            assert "Test error message" in payload["body"]
            assert "Test traceback details" in payload["body"]
            assert "bug" in payload["labels"]
            assert "auto-generated" in payload["labels"]

    async def test_create_issue_default_labels(self, reporter):
        """Test that default labels are applied when none provided."""
        reporter._get_installation_token = AsyncMock(return_value="valid-token")
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/test-org/test-repo/issues/456"
        }
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            await reporter.create_issue(
                module_name="test.module",
                error_msg="Test error",
                traceback_str=""
            )
            
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            
            assert "bug" in payload["labels"]
            assert "auto-generated" in payload["labels"]
            assert "severity:high" in payload["labels"]

    async def test_create_issue_custom_severity(self, reporter):
        """Test issue creation with custom severity."""
        reporter._get_installation_token = AsyncMock(return_value="valid-token")
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/test-org/test-repo/issues/789"
        }
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            await reporter.create_issue(
                module_name="critical.module",
                error_msg="Critical failure",
                traceback_str="",
                severity="critical"
            )
            
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            
            assert "🚨 CRITICAL:" in payload["title"]
            assert "severity:critical" in payload["labels"]

    async def test_create_issue_truncates_long_messages(self, reporter):
        """Test that long error messages and tracebacks are truncated."""
        reporter._get_installation_token = AsyncMock(return_value="valid-token")
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/test-org/test-repo/issues/999"
        }
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        long_error = "x" * 5000  # 5000 chars
        long_tb = "y" * 5000  # 5000 chars
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            await reporter.create_issue(
                module_name="test.module",
                error_msg=long_error,
                traceback_str=long_tb
            )
            
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            
            # Error should be truncated to ~2000 chars
            assert len(payload["body"].split("### Error Message")[1].split("### Traceback")[0]) <= 2100
            # Traceback should be truncated to ~3000 chars

    async def test_create_issue_api_failure(self, reporter):
        """Test issue creation handles API failure gracefully."""
        reporter._get_installation_token = AsyncMock(return_value="valid-token")
        
        mock_response = MagicMock()
        mock_response.status_code = 403  # Forbidden
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await reporter.create_issue(
                module_name="test.module",
                error_msg="Test error",
                traceback_str=""
            )
            
            assert result is None

    async def test_create_issue_exception_handling(self, reporter):
        """Test issue creation handles exceptions gracefully."""
        reporter._get_installation_token = AsyncMock(return_value="valid-token")
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await reporter.create_issue(
                module_name="test.module",
                error_msg="Test error",
                traceback_str=""
            )
            
            assert result is None


class TestIssueReporterIntegration:
    """Integration tests for GitHubIssueReporter workflow."""

    @pytest.mark.asyncio
    async def test_reporter_workflow_complete(self, reporter):
        """Test complete issue reporting workflow."""
        # Setup mocks
        reporter._get_installation_token = AsyncMock(return_value="valid-token")
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/test-org/test-repo/issues/100"
        }
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            # First report - should create issue
            result1 = await reporter.create_issue(
                module_name="survey.flow",
                error_msg="Selector timeout",
                traceback_str="File survey_flow.py, line 50"
            )
            
            assert result1 is not None
            
            # Second report with same error - should be deduplicated
            result2 = await reporter.create_issue(
                module_name="survey.flow",
                error_msg="Selector timeout",
                traceback_str="File survey_flow.py, line 50"
            )
            
            assert result2 is None
            
            # Third report with different error - should create new issue
            result3 = await reporter.create_issue(
                module_name="survey.flow",
                error_msg="Different error",
                traceback_str="File survey_flow.py, line 100"
            )
            
            assert result3 is not None
