"""PlayStealth Resilience Config - Zentrale Konfiguration für Resilienz."""
import os
from typing import Optional, Any
from dataclasses import dataclass


@dataclass
class ResilienceConfig:
    """Konfiguration für Resilienz-Features."""
    
    # Retry settings
    max_retries: int = 3
    retry_delay_base: float = 1.0
    retry_delay_max: float = 10.0
    
    # Timeout settings
    navigation_timeout: int = 30000
    action_timeout: int = 15000
    request_timeout: int = 60000
    
    # Fallback settings
    enable_fallback_selectors: bool = True
    enable_smart_resolver: bool = True
    
    # Circuit breaker
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_timeout: int = 60
    
    # Reporting
    enable_github_reporting: bool = False
    enable_auto_heal: bool = False
    
    # Telemetry
    enable_telemetry: bool = True
    telemetry_batch_size: int = 10
    
    @classmethod
    def from_env_or_args(cls, args: Optional[Any] = None) -> "ResilienceConfig":
        """Erstellt Config aus ENV oder CLI Args."""
        return cls(
            max_retries=int(os.getenv("PLAYSTEALTH_MAX_RETRIES", getattr(args, "max_retries", 3) or 3)),
            navigation_timeout=int(os.getenv("PLAYSTEALTH_NAV_TIMEOUT", 30000)),
            action_timeout=int(os.getenv("PLAYSTEALTH_ACTION_TIMEOUT", 15000)),
            enable_github_reporting=os.getenv("GITHUB_APP_ID") is not None,
            enable_auto_heal=os.getenv("PLAYSTEALTH_AUTO_HEAL", "false").lower() == "true",
            enable_telemetry=os.getenv("PLAYSTEALTH_TELEMETRY", "true").lower() != "false"
        )


_global_config: Optional[ResilienceConfig] = None


def set_global_config(cfg: ResilienceConfig):
    """Setzt globale Config für alle Module."""
    global _global_config
    _global_config = cfg


def get_global_config() -> ResilienceConfig:
    """Holt globale Config."""
    global _global_config
    if _global_config is None:
        _global_config = ResilienceConfig()
    return _global_config
