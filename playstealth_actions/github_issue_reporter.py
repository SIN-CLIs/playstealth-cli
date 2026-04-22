"""GitHub Issue Reporter for PlayStealth CLI."""
import os
import time
import hashlib
import jwt
import httpx
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timezone

class GitHubIssueReporter:
    """Auto-report module failures to GitHub Issues via GitHub App."""
    
    def __init__(self):
        self.app_id = os.getenv("GITHUB_APP_ID")
        self.private_key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
        self.installation_id = os.getenv("GITHUB_APP_INSTALLATION_ID")
        self.repo_owner = os.getenv("GITHUB_REPO_OWNER", "SIN-CLIs")
        self.repo_name = os.getenv("GITHUB_REPO_NAME", "playstealth-cli")
        self._token: Optional[str] = None
        self._token_expires: float = 0
        self._reported_hashes: set = set()
        self._enabled = all([self.app_id, self.private_key_path, self.installation_id])

    def _load_private_key(self) -> str:
        if not self.private_key_path:
            raise ValueError("GITHUB_APP_PRIVATE_KEY_PATH not set")
        return Path(self.private_key_path).read_text()

    def _generate_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 600, "iss": self.app_id}
        return jwt.encode(payload, self._load_private_key(), algorithm="RS256")

    async def _get_installation_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        app_jwt = self._generate_jwt()
        url = f"https://api.github.com/app/installations/{self.installation_id}/access_tokens"
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(url, headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github.v3+json"
            })
            res.raise_for_status()
            data = res.json()
            self._token = data["token"]
            self._token_expires = time.time() + 3000
            return self._token

    def _dedup_hash(self, module: str, error: str) -> str:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return hashlib.sha256(f"{module}:{error}:{day}".encode()).hexdigest()[:12]

    def _get_template_type(self, module_name: str, error_msg: str) -> str:
        ctx = f"{module_name} {error_msg}".lower()
        if any(kw in ctx for kw in ["selector", "dom", "scan", "click", "resolve", "locator", "timeout", "visible"]):
            return "selector_update"
        return "bug_report"

    def _format_body(self, template: str, module_name: str, error_msg: str, tb: str, sid: str, critical: bool) -> str:
        base = f"""### 🔧 Module: `{module_name}`
**Error:** `{error_msg}`
**Session:** `{sid}`
**Critical:** `{'Yes' if critical else 'No'}`
**Timestamp:** `{datetime.now(timezone.utc).isoformat()}Z`

### 📜 Traceback
```python
{tb[:1200]}
```
"""
        if template == "selector_update":
            return base + """
### 🎯 Selector/DOM Context
- [ ] Prüfe, ob sich die DOM-Struktur der Zielplattform geändert hat
- [ ] Validiere CSS/XPath/Text-Heuristiken mit `playstealth profile <url>`
- [ ] Fallback-Selektoren in `smart_selector.py` oder Plugin anpassen
- [ ] Ggf. `playstealth queue blacklist-add` bei persistenter Plattform-Änderung

> Auto-reported by PlayStealth Resilience Engine. Fallback applied. Telemetry logged.
"""
        return base + """
### 🐛 Bug Context
- [ ] Prüfe Netzwerk/Proxy-State & Playwright-Binary-Version
- [ ] Validiere `.env` Secrets & GitHub App Permissions
- [ ] Prüfe `telemetry.jsonl` auf vorangehende Module-Failures
- [ ] Bei State/Resume-Fehlern: `.playstealth_state/` bereinigen & neu starten

> Auto-reported by PlayStealth Resilience Engine. Fallback applied. Telemetry logged.
"""

    async def create_issue(self, module_name: str, error_msg: str, traceback_str: str, session_id: str, critical: bool = False, no_dedup: bool = False) -> Optional[str]:
        if not self._enabled:
            return None
        
        h = self._dedup_hash(module_name, error_msg)
        if not no_dedup and h in self._reported_hashes:
            return None
        self._reported_hashes.add(h)

        template = self._get_template_type(module_name, error_msg)
        title = f"🐛 [{module_name.split('.')[-1]}] {template.replace('_', ' ').title()}: {error_msg[:60]}"
        labels = ["bug", "auto-reported", module_name.split(".")[0], template]
        if critical:
            labels.append("critical")

        body = self._format_body(template, module_name, error_msg, traceback_str, session_id, critical)
        
        try:
            token = await self._get_installation_token()
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues"
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(url, json={"title": title, "body": body, "labels": labels}, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3+json"
                })
                res.raise_for_status()
                return res.json().get("html_url")
        except Exception as e:
            print(f"⚠️ GitHub issue creation failed: {e}")
            return None
