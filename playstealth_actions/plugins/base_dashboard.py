"""Abstract base for dashboard plugins."""
import abc
from playwright.async_api import Page
from typing import Dict, List, Any, Optional

class BaseDashboardPlugin(abc.ABC):
    """Abstrakte Basis für plattformspezifische Dashboard-Operationen."""

    @abc.abstractmethod
    async def login(self, page: Page, email: str, password: str) -> bool:
        """Führt Login durch. Return True bei Erfolg."""
        pass

    @abc.abstractmethod
    async def scan_surveys(self, page: Page) -> List[Dict[str, Any]]:
        """Extrahiert verfügbare Umfragen. Muss Liste von Dicts mit mind. 'id', 'title', 'selector' zurückgeben."""
        pass

    @abc.abstractmethod
    async def select_survey(self, page: Page, survey_id: str) -> bool:
        """Klickt auf Survey-Start. Return True bei Erfolg."""
        pass

    @abc.abstractmethod
    async def handle_screening_gate(self, page: Page, max_steps: int = 3) -> Dict[str, Any]:
        """Erkennt Disqualification-Gates & routed zurück. Return {'status': 'passed'|'disqualified', 'step': int}."""
        pass

    @abc.abstractmethod
    async def get_account_status(self, page: Page) -> Dict[str, Any]:
        """Extrahiert Balance, Pending, Profile-Status."""
        pass
