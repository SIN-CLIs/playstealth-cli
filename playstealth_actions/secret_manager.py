"""PlayStealth Secret Manager - Infisical runtime secret injection."""
import os
import re
import httpx
from typing import Dict, Optional, Set
from pathlib import Path


class SecretManager:
    """Lädt Secrets von Infisical zur Laufzeit, injiziert sicher, redaktiert Logs."""
    
    def __init__(self):
        self.project_id = os.getenv("INFISICAL_PROJECT_ID")
        self.token = os.getenv("INFISICAL_TOKEN")
        self.env_slug = os.getenv("INFISICAL_ENV", "prod")
        self._cache: Dict[str, str] = {}
        self._loaded = False
        self._enabled = bool(self.project_id and self.token)
        self._redact_patterns = [
            re.compile(r'(password|secret|key|token|pem|auth)[\s]*[:=][\s]*[^\s]+', re.I)
        ]

    async def load(self) -> bool:
        """Lädt Secrets von Infisical API."""
        if not self._enabled or self._loaded:
            return self._loaded
        
        try:
            url = f"https://app.infisical.com/api/v3/secrets?workspaceId={self.project_id}&environment={self.env_slug}"
            headers = {"Authorization": f"Bearer {self.token}"}
            
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    for s in data.get("secrets", []):
                        self._cache[s["key"]] = s["value"]
                    self._loaded = True
                    return True
        except Exception as e:
            print(f"⚠️ Infisical load failed: {e}")
        
        return False

    def get(self, key: str, fallback: Optional[str] = None) -> Optional[str]:
        """Holt Secret aus Cache oder Fallback zu os.environ."""
        return self._cache.get(key) or os.getenv(key, fallback)

    def inject_env(self):
        """Injiziert Secrets in os.environ (nur wenn noch nicht gesetzt)."""
        for k, v in self._cache.items():
            os.environ.setdefault(k, v)

    def redact(self, text: str) -> str:
        """Redaktiert Secrets in Logs/Outputs."""
        for pat in self._redact_patterns:
            text = pat.sub(lambda m: m.group(0).split(m.group(0)[-1])[0] + "****", text)
        return text

    def is_enabled(self) -> bool:
        """Prüft ob SecretManager aktiv ist."""
        return self._enabled
