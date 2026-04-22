# playstealth_actions/config_validator.py
import os
import sys
import subprocess
from pathlib import Path
import importlib.util
from typing import Dict, List, Any

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - optional runtime dependency
    dotenv_values = None

REQUIRED_ENV_KEYS = []
OPTIONAL_ENV_KEYS = [
    "PLAYSTEALTH_HEADLESS",
    "PLAYSTEALTH_PROXY_POOL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "PLAYSTEALTH_DOCKER",
]


def validate_env(env_path: str = ".env") -> Dict[str, Any]:
    """Prüft .env auf required/optional Keys."""
    if not Path(env_path).exists():
        return {
            "status": "warning",
            "msg": f"{env_path} nicht gefunden. Nutze System-Env oder lege .env an.",
        }

    if dotenv_values is not None:
        env = dotenv_values(env_path)
    else:
        env = dict(os.environ)
    missing_required = [k for k in REQUIRED_ENV_KEYS if k not in env or not env[k].strip()]
    missing_optional = [k for k in OPTIONAL_ENV_KEYS if k not in env or not env[k].strip()]

    if missing_required:
        return {
            "status": "error",
            "msg": f"Fehlende required Env-Vars: {missing_required}",
            "missing": missing_required,
        }

    return {
        "status": "ok",
        "msg": "Env-Validierung erfolgreich.",
        "warnings": [f"Optional missing: {k}" for k in missing_optional]
        if missing_optional
        else [],
    }


def validate_playwright_binaries() -> Dict[str, Any]:
    """Prüft, ob Playwright CLI & Chromium-Binaries vorhanden sind."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {
            "status": "error",
            "msg": "Playwright Python-Package nicht installiert. `pip install playwright`",
        }

    # Check CLI
    cli_check = subprocess.run(
        [sys.executable, "-m", "playwright", "--version"], capture_output=True, text=True
    )
    if cli_check.returncode != 0:
        return {
            "status": "error",
            "msg": "Playwright CLI nicht erreichbar. `pip install playwright`",
        }

    # Browser cache paths vary across OS, CI, and Playwright configuration.
    # We therefore keep validation lightweight and let real browser launch be the
    # final source of truth.
    return {
        "status": "ok",
        "msg": "Playwright CLI erreichbar; Browser-Verfügbarkeit wird beim Launch geprüft.",
    }


def validate_plugin_dependencies(plugin_modules: List[str]) -> Dict[str, Any]:
    """Prüft, ob alle Plugin-Imports verfügbar sind."""
    missing = []
    for mod in plugin_modules:
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)

    if missing:
        return {
            "status": "error",
            "msg": f"Fehlende Plugin-Dependencies: {missing}",
            "missing": missing,
        }
    return {"status": "ok", "msg": "Plugin-Dependencies OK"}


def validate_directories() -> Dict[str, Any]:
    """Prüft State- & Manifest-Verzeichnisse auf Existenz & Schreibrechte."""
    state_dir = Path(os.getenv("PLAYSTEALTH_STATE_DIR", ".playstealth_state"))
    manifest_dir = Path(os.getenv("PLAYSTEALTH_MANIFEST_PATH", ".playstealth_manifest.json")).parent

    errors = []
    for d in [state_dir, manifest_dir]:
        d.mkdir(parents=True, exist_ok=True)
        if not os.access(d, os.W_OK):
            errors.append(f"Keine Schreibrechte für {d}")

    if errors:
        return {"status": "error", "msg": "Verzeichnisrechte fehlerhaft", "errors": errors}
    return {"status": "ok", "msg": "Verzeichnisse OK"}


def run_full_validation(plugin_modules: List[str] = None) -> Dict[str, Any]:
    """Führt alle Checks aus und aggregiert das Ergebnis."""
    plugin_modules = plugin_modules or []
    results = {
        "env": validate_env(),
        "playwright": validate_playwright_binaries(),
        "plugins": validate_plugin_dependencies(plugin_modules),
        "directories": validate_directories(),
    }

    errors = [k for k, v in results.items() if v["status"] == "error"]
    warnings = [v["msg"] for v in results.values() if v["status"] == "warning"]
    warnings.extend([w for v in results.values() for w in v.get("warnings", [])])

    return {
        "valid": len(errors) == 0,
        "errors": {k: results[k]["msg"] for k in errors},
        "warnings": warnings,
        "details": results,
    }


if __name__ == "__main__":
    # Test run
    result = run_full_validation(
        ["playstealth_actions.plugins.hey_piggy", "playstealth_actions.plugins.qualtrics"]
    )
    print(f"Valid: {result['valid']}")
    if result["errors"]:
        print(f"Errors: {result['errors']}")
    if result["warnings"]:
        print(f"Warnings: {result['warnings']}")
