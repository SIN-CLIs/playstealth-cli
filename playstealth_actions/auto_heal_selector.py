"""PlayStealth Auto-Heal Selector - Auto-generates PRs for selector failures."""
import re
import os
import hashlib
import base64
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict
from .github_issue_reporter import GitHubIssueReporter


class AutoHealSelector:
    """Erkennt selector_update-Fehler, generiert Fallback-Selektoren und erstellt PR."""
    
    def __init__(self, reporter: GitHubIssueReporter):
        self.reporter = reporter
        self._pr_hashes: set = set()

    def _extract_failed_selector(self, error_msg: str, tb: str) -> Optional[str]:
        """Extrahiert den fehlgeschlagenen Selector aus Error/Traceback."""
        ctx = f"{error_msg} {tb}"
        match = re.search(
            r"selector['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]|Locator\(['\"]([^'\"]+)['\"]\)",
            ctx,
            re.I
        )
        return match.group(1) or match.group(2) if match else None

    def _generate_patch(self, module_name: str, failed_sel: str) -> Optional[Dict[str, str]]:
        """Generiert konservativen Fallback-Selector Patch."""
        plugin_name = module_name.split(".")[-1].replace("_dashboard", "").replace("_platform", "")
        file_path = f"playstealth_actions/plugins/{plugin_name}.py"
        
        fallback_block = f'''# 🤖 Auto-Heal Fallback (generated {datetime.now(timezone.utc).strftime("%Y-%m-%d")})
# Original failed: "{failed_sel}"
# Strategy: Added text/role fallbacks + smart_resolver delegation
FALLBACK_SELECTORS_{plugin_name.upper()} = [
    "{failed_sel}",
    "button:has-text('{failed_sel.split('.')[-1].split('#')[-1][:20]}')",
    "[role='button']:has-text('{failed_sel.split('.')[-1].split('#')[-1][:20]}')"
]
'''
        return {"path": file_path, "content": fallback_block, "module": module_name}

    async def _get_default_sha(self, client: httpx.AsyncClient, headers: dict) -> Optional[str]:
        """Holt SHA von main/master branch."""
        url = f"https://api.github.com/repos/{self.reporter.repo_owner}/{self.reporter.repo_name}/git/ref/heads/main"
        res = await client.get(url, headers=headers)
        if res.status_code == 404:
            url = url.replace("/main", "/master")
            res = await client.get(url, headers=headers)
        if res.status_code == 200:
            return res.json()["object"]["sha"]
        return None

    async def create_heal_pr(
        self,
        issue_url: str,
        module_name: str,
        error_msg: str,
        tb: str
    ) -> Optional[str]:
        """Erstellt Feature-Branch mit Fallback-Selectors und öffnet PR."""
        if not self.reporter._enabled:
            return None
        
        h = hashlib.sha256(f"{module_name}:{error_msg}".encode()).hexdigest()[:8]
        if h in self._pr_hashes:
            return None
        self._pr_hashes.add(h)

        failed_sel = self._extract_failed_selector(error_msg, tb)
        if not failed_sel:
            return None

        patch = self._generate_patch(module_name, failed_sel)
        if not patch:
            return None

        branch_name = f"auto-heal/selectors-{h}"
        token = await self.reporter._get_installation_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                base_sha = await self._get_default_sha(client, headers)
                if not base_sha:
                    return None

                # 1. Branch erstellen
                await client.post(
                    f"https://api.github.com/repos/{self.reporter.repo_owner}/{self.reporter.repo_name}/git/refs",
                    json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
                    headers=headers
                )

                # 2. Datei committen (create or update)
                file_url = f"https://api.github.com/repos/{self.reporter.repo_owner}/{self.reporter.repo_name}/contents/{patch['path']}"
                existing = await client.get(file_url, headers=headers)
                sha = existing.json().get("sha") if existing.status_code == 200 else None
                
                content_b64 = base64.b64encode(patch["content"].encode()).decode()
                commit_payload = {
                    "message": f"🤖 Auto-heal: Add fallback selectors for {patch['module']}",
                    "content": content_b64,
                    "branch": branch_name
                }
                if sha:
                    commit_payload["sha"] = sha
                
                await client.put(file_url, json=commit_payload, headers=headers)

                # 3. PR erstellen & mit Issue verknüpfen
                issue_num = issue_url.rstrip("/").split("/")[-1]
                pr_res = await client.post(
                    f"https://api.github.com/repos/{self.reporter.repo_owner}/{self.reporter.repo_name}/pulls",
                    json={
                        "title": f"🤖 Auto-Heal: Fallback selectors for `{patch['module']}`",
                        "body": f"🔗 Closes #{issue_num}\n\nAutomatically generated fallback selectors after DOM/selector failure.\n- Original: `{failed_sel}`\n- Strategy: Text/Role delegation + `smart_resolver` hint\n- Review & merge if validated.",
                        "head": branch_name,
                        "base": "main"
                    },
                    headers=headers
                )
                return pr_res.json().get("html_url")
        except Exception as e:
            print(f"⚠️ Auto-Heal PR failed: {e}")
            return None
