"""Resilience wrapper for PlayStealth CLI modules."""
import asyncio
import traceback
import concurrent.futures
from typing import Any, Callable, Optional, Dict
from .telemetry import log_event, generate_session_id
from .github_issue_reporter import GitHubIssueReporter
from .resilience_config import get_global_config

_reporter = GitHubIssueReporter()

async def run_resilient(
    func: Callable, *args,
    module_name: str = "unknown",
    fallback: Any = None,
    critical: bool = False,
    session_id: Optional[str] = None,
    config: Optional[object] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Execute a module with resilience handling.
    
    On error:
    - Log to telemetry
    - Create GitHub issue (non-blocking)
    - Return fallback value
    - Only break flow if critical=True or fail_fast config
    """
    cfg = config or get_global_config()
    sid = session_id or generate_session_id()
    try:
        result = await func(*args, **kwargs)
        return {"success": True, "data": result, "error": None, "critical": False}
    except Exception as e:
        tb = traceback.format_exc()
        error_msg = f"{type(e).__name__}: {str(e)}"
        log_event(sid, "module_failure", platform=module_name, error_code=error_msg, metadata={"traceback": tb[:400]})
        
        if cfg.auto_report:
            asyncio.create_task(_reporter.create_issue(
                module_name=module_name, error_msg=error_msg, traceback_str=tb,
                session_id=sid, critical=critical, no_dedup=cfg.no_issue_dedup
            ))
        
        if cfg.fail_fast or critical:
            return {"success": False, "data": None, "error": error_msg, "critical": True}
        return {"success": False, "data": fallback, "error": error_msg, "critical": False}


def run_resilient_sync(
    func: Callable, *args,
    module_name: str = "unknown",
    fallback: Any = None,
    critical: bool = False,
    session_id: Optional[str] = None,
    config: Optional[object] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Synchronous version of run_resilient for legacy modules.
    Reporter runs non-blocking in a thread.
    """
    cfg = config or get_global_config()
    sid = session_id or generate_session_id()
    try:
        result = func(*args, **kwargs)
        return {"success": True, "data": result, "error": None, "critical": False}
    except Exception as e:
        tb = traceback.format_exc()
        error_msg = f"{type(e).__name__}: {str(e)}"
        log_event(sid, "module_failure", platform=module_name, error_code=error_msg, metadata={"traceback": tb[:400]})
        
        if cfg.auto_report:
            def _fire_report():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(_reporter.create_issue(
                    module_name=module_name, error_msg=error_msg, traceback_str=tb,
                    session_id=sid, critical=critical, no_dedup=cfg.no_issue_dedup
                ))
                loop.close()
            concurrent.futures.ThreadPoolExecutor().submit(_fire_report)
        
        if cfg.fail_fast or critical:
            return {"success": False, "data": None, "error": error_msg, "critical": True}
        return {"success": False, "data": fallback, "error": error_msg, "critical": False}
