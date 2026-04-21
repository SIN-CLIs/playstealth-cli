"""Pytest defaults for deterministic config loading."""

from __future__ import annotations

import os


# Tests should not depend on the repository's live saved credentials unless a
# specific test opts in explicitly. Production runs still load the saved files.
os.environ.setdefault("HEYPIGGY_DISABLE_SAVED_ENV", "1")
