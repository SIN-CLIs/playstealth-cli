"""Guard the stability of ``worker``'s top-level public API.

If one of these assertions breaks, it means we accidentally renamed or
removed something that downstream consumers are allowed to import as
``from worker import ...``. Either add it back or bump the major version.
"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest

# Names that must be importable directly from the top-level package.
EXPECTED_PUBLIC_NAMES: frozenset[str] = frozenset(
    {
        # Entrypoints
        "run_worker",
        "WorkerContext",
        "current_context",
        # Utilities
        "configure_logging",
        "get_logger",
        "retry",
        "RetryPolicy",
        "AuditLogger",
        "ShutdownController",
        # Version
        "__version__",
        # Exceptions (the full public hierarchy)
        "WorkerError",
        "ConfigurationError",
        "PreflightError",
        "ShutdownRequested",
        "BridgeError",
        "BridgeTimeoutError",
        "BridgeProtocolError",
        "BridgeUnavailableError",
        "VisionError",
        "VisionTimeoutError",
        "VisionRateLimitError",
        "VisionCircuitOpenError",
        "ActionError",
        "ActionTimeoutError",
        "ActionBlockedError",
        "ElementNotFoundError",
    }
)


@pytest.fixture
def worker_module() -> ModuleType:
    return importlib.import_module("worker")


class TestPublicApi:
    def test_all_is_sorted(self, worker_module: ModuleType) -> None:
        """``__all__`` should be sorted so diffs stay clean."""
        all_: list[str] = list(worker_module.__all__)
        assert all_ == sorted(all_), "worker.__all__ must be sorted"

    def test_all_matches_expected(self, worker_module: ModuleType) -> None:
        all_: set[str] = set(worker_module.__all__)
        missing = EXPECTED_PUBLIC_NAMES - all_
        extra = all_ - EXPECTED_PUBLIC_NAMES
        assert not missing, f"public API regression, missing: {sorted(missing)}"
        assert not extra, f"unexpected new public names (document them): {sorted(extra)}"

    def test_all_names_are_importable(self, worker_module: ModuleType) -> None:
        for name in worker_module.__all__:
            assert hasattr(worker_module, name), f"worker.__all__ lists missing attr: {name}"

    def test_version_is_semver_shaped(self, worker_module: ModuleType) -> None:
        version: str = worker_module.__version__
        parts = version.split(".")
        assert len(parts) == 3, f"expected MAJOR.MINOR.PATCH, got {version!r}"
        for p in parts:
            # Allow pre-release markers like "0rc1".
            stripped = p.split("-")[0].rstrip("abrc0123456789")
            assert stripped == "" or stripped.isalpha(), f"bad version component: {p!r}"

    def test_importing_star_works(self) -> None:
        namespace: dict[str, object] = {}
        exec("from worker import *", namespace)  # noqa: S102
        # `from X import *` honours __all__, so everything we listed should land.
        landed = set(namespace) - {"__builtins__"}
        missing = EXPECTED_PUBLIC_NAMES - landed
        assert not missing, f"from worker import * missed: {sorted(missing)}"
