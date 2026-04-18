"""Entry point for ``python -m worker``.

Delegates to :func:`worker.cli.main` so that both of these work identically::

    python -m worker
    heypiggy-worker        # via the console_scripts entry point
"""

from __future__ import annotations

from worker.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
