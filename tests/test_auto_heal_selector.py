"""Test suite for AutoHealSelector module."""
import os
import pytest
import hashlib
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from playstealth_actions.auto_heal_selector import AutoHealSelector
from playstealth_actions.github_issue_reporter import GitHubIssueReporter


@pytest.fixture
def mock_reporter():
    """Create a mock GitHubIssueReporter for testing."""
    reporter = GitHubIssueReporter()
    reporter._enabled = True
    reporter.repo_owner = "test-org"
    reporter.repo_name = "test-repo"
    reporter._get_installation_token = AsyncMock(return_value="test-token-123")
    return reporter


@pytest.fixture
def auto_healer(mock_reporter):
    """Create AutoHealSelector instance with mock reporter."""
    return AutoHealSelector(mock_reporter)


class TestAutoHealSelectorInit:
    """Tests for AutoHealSelector initialization."""

    def test_init_with_reporter(self, mock_reporter):
        """Test initialization with GitHubIssueReporter."""
        healer = AutoHealSelector(mock_reporter)
        assert healer.reporter == mock_reporter
        assert healer._pr_hashes == set()


class TestExtractFailedSelector:
    """Tests for _extract_failed_selector method."""

    def test_extract_selector_with_colon(self, auto_healer):
        """Test extracting selector with colon syntax."""
        error_msg = "TimeoutError: selector='button.submit-btn' not found"
        tb = "File test.py, line 10, in test_func"
        
        result = auto_healer._extract_failed_selector(error_msg, tb)
        assert result == "button.submit-btn"

    def test_extract_selector_with_equals(self, auto_healer):
        """Test extracting selector with equals syntax."""
        error_msg = "ElementNotFound: selector = \"#main-content .header\""
        tb = ""
        
        result = auto_healer._extract_failed_selector(error_msg, tb)
        assert result == "#main-content .header"

    def test_extract_locator_syntax(self, auto_healer):
        """Test extracting selector from Locator() syntax."""
        error_msg = "Error in test"
        tb = "Locator('div.container > button.primary')"
        
        result = auto_healer._extract_failed_selector(error_msg, tb)
        assert result == "div.container > button.primary"

    def test_extract_selector_single_quotes(self, auto_healer):
        """Test extracting selector with single quotes."""
        error_msg = "selector: '.nav-item.active' timeout"
        tb = ""
        
        result = auto_healer._extract_failed_selector(error_msg, tb)
        assert result == ".nav-item.active"

    def test_extract_no_selector(self, auto_healer):
        """Test when no selector pattern is found."""
        error_msg = "Generic error without selector info"
        tb = "Some traceback without locator info"
        
        result = auto_healer._extract_failed_selector(error_msg, tb)
        assert result is None

    def test_extract_from_complex_traceback(self, auto_healer):
        """Test extracting from complex traceback with multiple lines."""
        error_msg = "PlaywrightTimeoutError"
        tb = """
        File "survey_flow.py", line 45, in click_next
            await page.click(selector="#next-button")
        File "playwright/_impl/_page.py", line 500, in click
            selector='button[type=\"submit\"]', timeout=5000
        TimeoutError: Element not found
        """
        
        result = auto_healer._extract_failed_selector(error_msg, tb)
        # Should find one of the selectors
        assert result is not None


class TestGeneratePatch:
    """Tests for _generate_patch method."""

    def test_generate_patch_basic(self, auto_healer):
        """Test basic patch generation."""
        module_name = "playstealth_actions.plugins.survey_dashboard"
        failed_sel = "button.submit-form"
        
        patch = auto_healer._generate_patch(module_name, failed_sel)
        
        assert patch is not None
        assert "path" in patch
        assert "content" in patch
        assert "module" in patch
        assert patch["module"] == module_name
        assert "survey" in patch["path"]
        assert "FALLBACK_SELECTORS_SURVEY" in patch["content"]
        assert failed_sel in patch["content"]

    def test_generate_patch_platform_module(self, auto_healer):
        """Test patch generation for platform module."""
        module_name = "playstealth_actions.plugins.reward_platform"
        failed_sel = "#claim-reward-btn"
        
        patch = auto_healer._generate_patch(module_name, failed_sel)
        
        assert patch is not None
        assert "reward" in patch["path"]
        assert "FALLBACK_SELECTORS_REWARD" in patch["content"]

    def test_generate_patch_fallback_selectors(self, auto_healer):
        """Test that fallback includes text/role strategies."""
        module_name = "test_module"
        failed_sel = ".complex.nested.selector#with-hash"
        
        patch = auto_healer._generate_patch(module_name, failed_sel)
        
        content = patch["content"]
        # Should include original selector
        assert failed_sel in content
        # Should include text-based fallback
        assert "button:has-text(" in content
        # Should include role-based fallback
        assert "[role='button']:has-text(" in content

    def test_generate_patch_date_stamped(self, auto_healer):
        """Test that patch includes generation date."""
        module_name = "test_module"
        failed_sel = ".test-selector"
        
        patch = auto_healer._generate_patch(module_name, failed_sel)
        
        content = patch["content"]
        assert "Auto-Heal Fallback" in content
        assert datetime.now(timezone.utc).strftime("%Y-%m-%d") in content


class TestGetDefaultSha:
    """Tests for _get_default_sha method."""

    @pytest.mark.asyncio
    async def test_get_sha_main_branch(self, auto_healer):
        """Test getting SHA from main branch."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": {"sha": "abc123def456"}
        }
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        
        headers = {"Authorization": "Bearer token"}
        result = await auto_healer._get_default_sha(mock_client, headers)
        
        assert result == "abc123def456"
        mock_client.get.assert_called_once()
        assert "/main" in mock_client.get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_sha_fallback_master(self, auto_healer):
        """Test fallback to master branch when main not found."""
        # First call returns 404, second returns 200
        response_404 = MagicMock()
        response_404.status_code = 404
        
        response_200 = MagicMock()
        response_200.status_code = 200
        response_200.json.return_value = {
            "object": {"sha": "xyz789master"}
        }
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[response_404, response_200])
        
        headers = {"Authorization": "Bearer token"}
        result = await auto_healer._get_default_sha(mock_client, headers)
        
        assert result == "xyz789master"
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_get_sha_not_found(self, auto_healer):
        """Test when neither main nor master exists."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        
        headers = {"Authorization": "Bearer token"}
        result = await auto_healer._get_default_sha(mock_client, headers)
        
        assert result is None


class TestCreateHealPr:
    """Tests for create_heal_pr method."""

    @pytest.mark.asyncio
    async def test_create_pr_disabled_reporter(self, mock_reporter):
        """Test PR creation when reporter is disabled."""
        mock_reporter._enabled = False
        healer = AutoHealSelector(mock_reporter)
        
        result = await healer.create_heal_pr(
            issue_url="https://github.com/test-org/test-repo/issues/123",
            module_name="test.module",
            error_msg="selector='test' failed",
            tb=""
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_create_pr_deduplication(self, auto_healer):
        """Test that duplicate PRs are prevented."""
        issue_url = "https://github.com/test-org/test-repo/issues/123"
        module_name = "test.module"
        error_msg = "selector='test' failed"
        
        # Create hash that would be generated
        h = hashlib.sha256(f"{module_name}:{error_msg}".encode()).hexdigest()[:8]
        
        # First call - should attempt PR creation (will fail due to mocking)
        # Second call with same params - should return None immediately
        auto_healer._pr_hashes.add(h)
        
        result = await auto_healer.create_heal_pr(
            issue_url=issue_url,
            module_name=module_name,
            error_msg=error_msg,
            tb=""
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_create_pr_no_selector_in_error(self, auto_healer):
        """Test PR creation fails gracefully when no selector found."""
        result = await auto_healer.create_heal_pr(
            issue_url="https://github.com/test-org/test-repo/issues/123",
            module_name="test.module",
            error_msg="Generic error without selector",
            tb=""
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_create_pr_full_flow(self, auto_healer, mock_reporter):
        """Test complete PR creation flow with mocked API."""
        issue_url = "https://github.com/test-org/test-repo/issues/123"
        module_name = "playstealth_actions.plugins.survey_dashboard"
        error_msg = "TimeoutError: selector='button.submit' not found"
        tb = "Locator('button.submit') in survey_flow.py"
        
        # Mock API responses - need proper sequencing for all calls
        sha_response = MagicMock()
        sha_response.status_code = 200
        sha_response.json.return_value = {"object": {"sha": "base-sha-123"}}
        
        file_check_response = MagicMock()
        file_check_response.status_code = 404  # File doesn't exist yet
        
        commit_response = MagicMock()
        commit_response.status_code = 200
        
        pr_response = MagicMock()
        pr_response.status_code = 201
        pr_response.json.return_value = {
            "html_url": "https://github.com/test-org/test-repo/pull/456"
        }
        
        mock_client = AsyncMock()
        # Fix: Proper sequence of API calls:
        # 1. GET /git/ref/heads/main -> sha_response
        # 2. POST /git/refs -> create branch (use sha_response)
        # 3. GET /contents/file -> file_check_response (404)
        # 4. PUT /contents/file -> commit_response
        # 5. POST /pulls -> pr_response
        mock_client.get = AsyncMock(side_effect=[sha_response, file_check_response])
        mock_client.post = AsyncMock(side_effect=[sha_response, pr_response])  # branch + PR
        mock_client.put = AsyncMock(return_value=commit_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await auto_healer.create_heal_pr(
                issue_url=issue_url,
                module_name=module_name,
                error_msg=error_msg,
                tb=tb
            )
            
            # Should return PR URL
            assert result == "https://github.com/test-org/test-repo/pull/456"
            
            # Verify API calls were made
            assert mock_client.post.call_count >= 2  # Branch + PR
            assert mock_client.get.call_count >= 2  # SHA + file check

    @pytest.mark.asyncio
    async def test_create_pr_exception_handling(self, auto_healer):
        """Test that exceptions during PR creation are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await auto_healer.create_heal_pr(
                issue_url="https://github.com/test-org/test-repo/issues/123",
                module_name="test.module",
                error_msg="selector='test' failed",
                tb=""
            )
            
            # Should return None on error, not raise exception
            assert result is None


class TestAutoHealIntegration:
    """Integration tests for AutoHealSelector workflow."""

    @pytest.mark.asyncio
    async def test_heal_workflow_complete(self, auto_healer, mock_reporter):
        """Test complete auto-heal workflow from error to PR."""
        # Simulate realistic error scenario
        issue_url = "https://github.com/test-org/test-repo/issues/999"
        module_name = "playstealth_actions.plugins.claim_dashboard"
        error_msg = "PlaywrightTimeoutError: selector='#claim-btn.disabled' timeout after 5000ms"
        tb = """
        File "claim_flow.py", line 78, in claim_reward
            await page.click(selector='#claim-btn.disabled')
        File "playwright/_impl/_page.py", line 500, in click
            Locator('#claim-btn.disabled')
        """
        
        # Extract selector first (unit test the extraction)
        failed_sel = auto_healer._extract_failed_selector(error_msg, tb)
        assert failed_sel is not None
        assert "#claim-btn.disabled" in failed_sel or "claim-btn" in failed_sel
        
        # Generate patch (unit test patch generation)
        patch = auto_healer._generate_patch(module_name, failed_sel)
        assert patch is not None
        assert "claim" in patch["path"]
        assert "FALLBACK_SELECTORS_CLAIM" in patch["content"]
