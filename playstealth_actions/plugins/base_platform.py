import abc
from playwright.async_api import Page
from typing import Dict, Any


class BasePlatform(abc.ABC):
    """Abstrakte Basis für alle Survey-Plattform-Plugins."""

    @property
    def platform_name(self) -> str:
        """Stable telemetry/debug name for this platform plugin."""
        return self.__class__.__name__

    @abc.abstractmethod
    async def detect(self, page: Page) -> bool:
        """Erkennt, ob die aktuelle Seite zu dieser Plattform gehört."""
        pass

    @abc.abstractmethod
    async def handle_consent(self, page: Page) -> bool:
        """Behandelt Cookie-/Privacy-Banner. Return True wenn erfolgreich."""
        pass

    @abc.abstractmethod
    async def get_current_step(self, page: Page) -> Dict[str, Any]:
        """Extrahiert aktuelle Frage/Optionen. Muss dict mit 'question', 'option_count', 'type' zurückgeben."""
        pass

    @abc.abstractmethod
    async def answer_question(self, page: Page, answer_data: Any) -> bool:
        """Wählt Antwort aus (Index oder Text). Return True bei Erfolg."""
        pass

    @abc.abstractmethod
    async def navigate_next(self, page: Page) -> bool:
        """Klickt Weiter/Submit. Return True bei Erfolg."""
        pass

    @abc.abstractmethod
    async def is_completed(self, page: Page) -> bool:
        """Prüft, ob die Umfrage abgeschlossen ist."""
        pass
