"""
State Store with Full Browser Context Persistence.

This module handles both CLI state (survey progress, metadata) and 
complete browser context persistence (cookies, localStorage, sessionStorage).
"""
import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

try:
    from playwright.async_api import Browser, BrowserContext, Playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# State directory configuration
STATE_DIR = Path(os.getenv("PLAYSTEALTH_STATE_DIR", ".playstealth_state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

CHROME_USER_DATA_DIR = Path.home() / "Library/Application Support/Google/Chrome"


def detect_chrome_profile_dir() -> str:
    """Pick the preferred Chrome profile directory.

    We prefer the explicit env override first, then fall back to common local
    profile names so the operator's logged-in Chrome state can be reused.
    """
    preferred = os.getenv("HEYPIGGY_CHROME_PROFILE_DIR", "Default")
    for candidate in [preferred, "Default", "Profile 18"]:
        if (CHROME_USER_DATA_DIR / candidate).exists():
            return candidate
    return preferred


def prepare_profile_root() -> Path:
    """Create or reuse a persistent Playwright-safe Chrome profile clone."""
    profile_root = Path(
        os.getenv(
            "PLAYSTEALTH_PROFILE_ROOT",
            str(Path.home() / ".heypiggy" / "playwright_profile_clone"),
        )
    )
    profile_root.mkdir(parents=True, exist_ok=True)
    profile_dir = detect_chrome_profile_dir()
    dst_profile = profile_root / profile_dir
    if dst_profile.exists() and any(dst_profile.iterdir()):
        return profile_root

    for name in ("Local State", "First Run", "Last Version"):
        src = CHROME_USER_DATA_DIR / name
        if src.exists():
            shutil.copy2(src, profile_root / name)

    src_profile = CHROME_USER_DATA_DIR / profile_dir
    if src_profile.exists():
        shutil.copytree(
            src_profile,
            dst_profile,
            symlinks=True,
            ignore_dangling_symlinks=True,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "SingletonLock",
                "SingletonCookie",
                "SingletonSocket",
                "RunningChromeVersion",
                "Crashpad",
                "GPUCache",
                "GrShaderCache",
                "ShaderCache",
                "Code Cache",
                "DawnCache",
                "Visited Links",
                "chrome_debug.log",
            ),
        )
    return profile_root


async def launch_persistent_profile_context(
    playwright: "Playwright",
    profile: Optional[Dict[str, Any]] = None,
) -> BrowserContext:
    """Launch a persistent context backed by the saved Chrome default profile.

    This is the operator-friendly path: one logged-in Chrome profile clone is
    reused across runs so we avoid repeated logins and keep debug sessions stable.
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is required for browser context management")

    from .stealth_enhancer import apply_stealth_profile, generate_user_agent

    if profile is None:
        profile = {
            "ua": generate_user_agent("windows", "chrome"),
            "locale": "de-DE",
            "timezone": "Europe/Berlin",
        }

    profile_root = prepare_profile_root()
    profile_dir = detect_chrome_profile_dir()
    headless = str(os.getenv("PLAYSTEALTH_HEADLESS", "true")).lower() in ("true", "1", "yes")
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_root),
        headless=headless,
        channel="chrome",
        viewport={"width": 1920, "height": 1080},
        locale=profile.get("locale", "de-DE"),
        timezone_id=profile.get("timezone", "Europe/Berlin"),
        user_agent=profile.get("ua"),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            f"--profile-directory={profile_dir}",
        ],
    )
    await apply_stealth_profile(context, profile)
    return context


def _session_dir(session_id: str) -> Path:
    """Get the directory path for a specific session."""
    return STATE_DIR / f"session_{session_id}"


def save_cli_state(session_id: str, metadata: Dict[str, Any]) -> None:
    """
    Save CLI-level state (survey progress, current step, metadata).
    
    Args:
        session_id: Unique identifier for the survey session
        metadata: Dictionary containing survey state (step, index, timestamps, etc.)
    """
    session_path = _session_dir(session_id)
    session_path.mkdir(parents=True, exist_ok=True)
    
    state_file = session_path / "state.json"
    metadata["updated_at"] = datetime.now().isoformat()
    
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def load_cli_state(session_id: str) -> Dict[str, Any]:
    """
    Load CLI-level state for a session.
    
    Args:
        session_id: Unique identifier for the survey session
        
    Returns:
        Dictionary containing survey state
        
    Raises:
        FileNotFoundError: If no state exists for this session
    """
    state_file = _session_dir(session_id) / "state.json"
    
    if not state_file.exists():
        raise FileNotFoundError(f"CLI state missing for session: {session_id}")
    
    with open(state_file, "r", encoding="utf-8") as f:
        return json.load(f)


async def save_browser_state(context: BrowserContext, session_id: str) -> str:
    """
    Save complete browser context state (cookies, localStorage, sessionStorage).
    
    Args:
        context: Playwright BrowserContext to save
        session_id: Unique identifier for the survey session
        
    Returns:
        Path to the saved storage state file
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is required for browser state management")
    
    session_path = _session_dir(session_id)
    session_path.mkdir(parents=True, exist_ok=True)
    
    storage_file = session_path / "storage_state.json"
    
    # Save complete browser context state
    await context.storage_state(path=str(storage_file))
    
    return str(storage_file)


async def load_browser_context(
    browser: Browser, 
    session_id: str, 
    profile: Optional[Dict[str, Any]] = None
) -> BrowserContext:
    """
    Load browser context from saved state with optional stealth profile.
    
    Args:
        browser: Playwright Browser instance
        session_id: Unique identifier for the survey session
        profile: Optional stealth profile (UA, timezone, locale, etc.)
        
    Returns:
        New BrowserContext with loaded state and applied profile
        
    Raises:
        FileNotFoundError: If no browser state exists for this session
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is required for browser context management")
    
    from .stealth_enhancer import apply_stealth_profile
    
    storage_file = _session_dir(session_id) / "storage_state.json"
    
    if not storage_file.exists():
        raise FileNotFoundError(f"Browser state missing for session: {session_id}")
    
    # Load stealth profile if not provided
    if profile is None:
        # Try to load profile from session metadata
        try:
            cli_state = load_cli_state(session_id)
            profile = cli_state.get("stealth_profile")
        except FileNotFoundError:
            profile = None
    
    # Create context with storage state
    context_kwargs = {
        "storage_state": str(storage_file),
        "viewport": {"width": 1920, "height": 1080},
        "java_script_enabled": True,
        "bypass_csp": True,
    }
    
    # Add profile settings if available
    if profile:
        if "ua" in profile or "user_agent" in profile:
            context_kwargs["user_agent"] = profile.get("ua") or profile.get("user_agent")
        if "locale" in profile:
            context_kwargs["locale"] = profile["locale"]
        if "timezone" in profile or "timezone_id" in profile:
            context_kwargs["timezone_id"] = profile.get("timezone") or profile.get("timezone_id")
    
    context = await browser.new_context(**context_kwargs)
    
    # Apply stealth profile injections
    if profile:
        await apply_stealth_profile(context, profile)
    else:
        # Apply default stealth if no profile
        await apply_stealth_profile(context)
    
    return context


async def create_fresh_context(
    browser: Browser,
    profile: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None
) -> BrowserContext:
    """
    Create a fresh browser context (no saved state) with stealth profile.
    
    Use this for new surveys or when starting without previous state.
    
    Args:
        browser: Playwright Browser instance
        profile: Stealth profile configuration
        session_id: Optional session ID to associate with context
        
    Returns:
        New BrowserContext with stealth profile applied
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is required for browser context management")
    
    from .stealth_enhancer import apply_stealth_profile, generate_user_agent
    
    # Generate or use provided profile
    if profile is None:
        profile = generate_user_agent()
    
    context_kwargs = {
        "viewport": {"width": 1920, "height": 1080},
        "java_script_enabled": True,
        "bypass_csp": True,
        "user_agent": profile.get("ua"),
        "locale": profile.get("locale", "de-DE"),
        "timezone_id": profile.get("timezone", "Europe/Berlin"),
    }
    
    context = await browser.new_context(**context_kwargs)
    
    # Apply stealth injections
    await apply_stealth_profile(context, profile)
    
    # Save profile to session state if session_id provided
    if session_id:
        try:
            cli_state = load_cli_state(session_id)
        except FileNotFoundError:
            cli_state = {}
        cli_state["stealth_profile"] = profile
        save_cli_state(session_id, cli_state)
    
    return context


def cleanup_session(session_id: str) -> bool:
    """
    Remove all state files for a session.
    
    Args:
        session_id: Unique identifier for the survey session
        
    Returns:
        True if session was cleaned up, False if it didn't exist
    """
    session_path = _session_dir(session_id)
    
    if session_path.exists():
        shutil.rmtree(session_path)
        return True
    
    return False


def list_sessions() -> list:
    """
    List all existing session IDs.
    
    Returns:
        List of session ID strings
    """
    sessions = []
    
    if not STATE_DIR.exists():
        return sessions
    
    for item in STATE_DIR.iterdir():
        if item.is_dir() and item.name.startswith("session_"):
            session_id = item.name.replace("session_", "")
            sessions.append(session_id)
    
    return sorted(sessions)


def get_session_info(session_id: str) -> Dict[str, Any]:
    """
    Get detailed information about a session.
    
    Args:
        session_id: Unique identifier for the survey session
        
    Returns:
        Dictionary with session metadata
    """
    session_path = _session_dir(session_id)
    
    info = {
        "session_id": session_id,
        "exists": session_path.exists(),
        "cli_state": None,
        "browser_state": False,
    }
    
    if not info["exists"]:
        return info
    
    # Check CLI state
    state_file = session_path / "state.json"
    if state_file.exists():
        try:
            info["cli_state"] = load_cli_state(session_id)
        except Exception:
            info["cli_state"] = {"error": "Could not load state"}
    
    # Check browser state
    storage_file = session_path / "storage_state.json"
    info["browser_state"] = storage_file.exists()
    
    return info
