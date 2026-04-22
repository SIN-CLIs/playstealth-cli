"""Resilience configuration for PlayStealth CLI."""
import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class ResilienceConfig:
    """Configuration for resilience features."""
    auto_report: bool = True
    fail_fast: bool = False
    no_issue_dedup: bool = False

    @classmethod
    def from_env_or_args(cls, args: Optional[object] = None) -> "ResilienceConfig":
        """Load config from environment variables or CLI args."""
        def _bool(val: str) -> bool:
            return str(val).lower() in ("true", "1", "yes")
        
        return cls(
            auto_report=_bool(getattr(args, "auto_report", os.getenv("PLAYSTEALTH_AUTO_REPORT", "true"))),
            fail_fast=_bool(getattr(args, "fail_fast", os.getenv("PLAYSTEALTH_FAIL_FAST", "false"))),
            no_issue_dedup=_bool(getattr(args, "no_issue_dedup", os.getenv("PLAYSTEALTH_NO_ISSUE_DEDUP", "false")))
        )

# Global singleton
_global_config: Optional[ResilienceConfig] = None

def set_global_config(cfg: ResilienceConfig):
    """Set global resilience configuration."""
    global _global_config
    _global_config = cfg

def get_global_config() -> ResilienceConfig:
    """Get global resilience configuration."""
    global _global_config
    if _global_config is None:
        _global_config = ResilienceConfig.from_env_or_args()
    return _global_config
