"""PlayStealth GitHub Issue Reporter - Auto-creates issues for failures."""
import os
import hashlib
import httpx
from typing import Optional, Set
from datetime import datetime, timezone, timedelta


class GitHubIssueReporter:
    """Erstellt GitHub Issues bei Fehlern mit Deduplication."""
    
    def __init__(self):
        self._enabled = bool(os.getenv("GITHUB_APP_ID") and os.getenv("GITHUB_APP_PRIVATE_KEY"))
        self.app_id = os.getenv("GITHUB_APP_ID")
        self._private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        self.installation_id = os.getenv("GITHUB_INSTALLATION_ID")
        self.repo_owner = os.getenv("GITHUB_REPO_OWNER", "SIN-CLIs")
        self.repo_name = os.getenv("GITHUB_REPO_NAME", "playstealth-cli")
        self._issue_hashes: Set[str] = set()
        self._token_cache: Optional[str] = None
        self._token_expires: Optional[datetime] = None

    async def _get_installation_token(self) -> Optional[str]:
        """Holt JWT Token für GitHub App Installation."""
        if self._token_cache and self._token_expires and datetime.now(timezone.utc) < self._token_expires:
            return self._token_cache
        
        try:
            import jwt
            now = datetime.now(timezone.utc)
            payload = {
                "iat": int(now.timestamp()) - 60,
                "exp": int(now.timestamp()) + 540,
                "iss": self.app_id
            }
            app_jwt = jwt.encode(payload, self._private_key, algorithm="RS256")
            
            headers = {"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github.v3+json"}
            url = f"https://api.github.com/app/installations/{self.installation_id}/access_tokens"
            
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(url, headers=headers)
                if res.status_code == 201:
                    data = res.json()
                    self._token_cache = data["token"]
                    self._token_expires = now + timedelta(minutes=8)
                    return self._token_cache
        except Exception as e:
            print(f"⚠️ GitHub token fetch failed: {e}")
        return None

    def _dedup_hash(self, module_name: str, error_msg: str) -> str:
        """Generiert dedup hash für issue."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return hashlib.sha256(f"{module_name}:{error_msg}:{day}".encode()).hexdigest()[:12]

    def _get_template_type(self, module_name: str, error_msg: str) -> str:
        """Bestimmt Issue Template Typ basierend auf Fehlerkontext."""
        ctx = f"{module_name} {error_msg}".lower()
        if any(kw in ctx for kw in ["selector", "dom", "scan", "click", "resolve", "locator", "timeout", "visible"]):
            return "selector_update"
        return "bug_report"

    def _format_body(self, template: str, module_name: str, error_msg: str, tb: str, session_id: str, critical: bool) -> str:
        """Formatiert Issue Body mit Template-spezifischen Inhalten."""
        base = f"""### 🔧 Module: `{module_name}`
**Error:** `{error_msg}`
**Session:** `{session_id}`
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

    async def create_issue(
        self,
        module_name: str,
        error_msg: str,
        traceback_str: str,
        session_id: str = "unknown",
        critical: bool = False,
        no_dedup: bool = False,
        severity: str = "high",
        labels: Optional[list] = None
    ) -> Optional[str]:
        """Erstellt GitHub Issue wenn noch nicht existent."""
        if not self._enabled:
            return None
        
        h = self._dedup_hash(module_name, error_msg)
        if not no_dedup and h in self._issue_hashes:
            return None
        self._issue_hashes.add(h)
        
        token = await self._get_installation_token()
        if not token:
            return None
        
        template = self._get_template_type(module_name, error_msg)
        title = f"🐛 [{module_name.split('.')[-1]}] {template.replace('_', ' ').title()}: {error_msg[:60]}"
        default_labels = ["bug", "auto-reported", module_name.split(".")[0], template, f"severity:{severity}"]
        if critical:
            default_labels.append("critical")
        if labels:
            default_labels.extend(labels)
        
        body = self._format_body(template, module_name, error_msg, traceback_str, session_id, critical)
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues"
                res = await client.post(url, json={
                    "title": title,
                    "body": body,
                    "labels": default_labels
                }, headers=headers)
                
                if res.status_code == 201:
                    return res.json()["html_url"]
        except Exception as e:
            print(f"⚠️ GitHub issue creation failed: {e}")
        
        return None
