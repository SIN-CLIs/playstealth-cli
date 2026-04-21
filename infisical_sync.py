"""Infisical sync helpers for OpenSIN agents.

WHY: Agents must never leave secrets or env vars scattered in local files.
This module normalizes env-like files, uploads them to Infisical, and can
emit redacted Global Brain facts so every agent learns the same state.

Design goals:
- preserve the original file content semantics as much as possible
- normalize export/YAML-ish syntax into standard KEY=VALUE pairs
- never log or store raw secret values in Brain facts
- keep the sync flow deterministic and testable
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from global_brain_client import GlobalBrainClient
from global_brain_policy import (
    InfisicalTarget,
    SecretDetection,
    SecretSource,
    ingest_secret_event,
    normalize_env_key,
)


_YAML_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*:\s*.*$")


def _slugify_segment(text: str) -> str:
    """Turn a folder/repo name into a stable Infisical path segment."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", cleaned) or "env"


@dataclass(slots=True)
class NormalizedEnvFile:
    """A normalized env file ready for Infisical import."""

    path: Path
    mapping: dict[str, str]


@dataclass(slots=True)
class SyncResult:
    """Summary of one env-file sync run."""

    source: Path
    target: InfisicalTarget
    secrets_synced: int
    normalized_file: Path
    brain_facts: int = 0


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """Parse one env-style line into a normalized key/value pair.

    Supports:
    - KEY=value
    - export KEY=value
    - YAML-ish KEY: value
    """
    raw = line.strip()
    if not raw or raw.startswith("#") or raw.startswith("//"):
        return None
    if raw.startswith("export "):
        raw = raw[len("export ") :].strip()

    if "=" in raw:
        key, value = raw.split("=", 1)
    elif ":" in raw and _YAML_KEY_RE.match(raw):
        key, value = raw.split(":", 1)
        value = value.lstrip()
    else:
        return None

    key = normalize_env_key(key)
    value = value.strip()
    if value in {'""', "''", ""}:
        # WHY: Infisical requires a non-empty value in the import file; we use
        # a sentinel that keeps the key present and can be interpreted by the
        # receiving workflow as intentionally empty.
        value = "__EMPTY__"
    return key, value


def parse_env_text(text: str) -> dict[str, str]:
    """Return a normalized KEY=VALUE mapping from env-like text."""
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        mapping[key] = value
    return mapping


def normalize_env_file(source: Path) -> NormalizedEnvFile:
    """Normalize an env-like source file into a portable import file."""
    mapping = parse_env_text(source.read_text(errors="replace"))
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".env")
    try:
        for key, value in mapping.items():
            tmp.write(f"{key}={value}\n")
    finally:
        tmp.close()
    return NormalizedEnvFile(path=Path(tmp.name), mapping=mapping)


def sync_env_file_to_infisical(
    source: Path,
    target: InfisicalTarget,
    *,
    token: str,
    domain: str = "https://eu.infisical.com",
    brain: GlobalBrainClient | None = None,
    repo: str = "",
    branch: str = "main",
    agent_id: str = "",
    origin: str = "local",
) -> SyncResult:
    """Upload a single env file to Infisical and optionally emit Brain facts."""
    normalized = normalize_env_file(source)
    try:
        cmd = [
            "infisical",
            "secrets",
            "set",
            f"--domain={domain}",
            "--token",
            token,
            f"--projectId={target.project_id}",
            f"--env={target.environment}",
            f"--path={target.folder}",
            f"--file={normalized.path}",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

        brain_facts = 0
        if brain is not None:
            for key, value in normalized.mapping.items():
                detection = SecretDetection(
                    key=key,
                    value=value,
                    classification="env" if key.isupper() else "secret",
                    source=SecretSource(
                        file=source.name,
                        path=str(source),
                        line=0,
                        origin=origin,
                    ),
                    repo=repo,
                    branch=branch,
                    agent_id=agent_id,
                )
                ingest_secret_event(
                    brain,
                    detection,
                    target,
                    event="sync_success",
                    status="verified",
                    notes="synced from env file",
                )
                brain_facts += 1

        return SyncResult(
            source=source,
            target=target,
            secrets_synced=len(normalized.mapping),
            normalized_file=normalized.path,
            brain_facts=brain_facts,
        )
    finally:
        try:
            os.unlink(normalized.path)
        except OSError:
            pass


def sync_roots(
    roots: Iterable[Path],
    *,
    token: str,
    project_id: str,
    environment: str,
    folder_root: str,
    brain: GlobalBrainClient | None = None,
    repo: str = "",
    branch: str = "main",
    agent_id: str = "",
    origin: str = "scan",
) -> list[SyncResult]:
    """Scan multiple roots and sync any env-like files discovered."""
    results: list[SyncResult] = []
    for root in roots:
        if not root.exists():
            continue
        repo_slug = _slugify_segment(root.name)
        target_repo_root = f"{folder_root.rstrip('/')}/{repo_slug}".rstrip("/")
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if not (
                name == ".env"
                or name.startswith(".env.")
                or name.endswith(".env")
                or name.endswith(".xcode.env")
            ):
                continue
            if any(part in {".git", "node_modules", ".cache", ".bun"} for part in path.parts):
                continue
            rel_dir = path.parent.relative_to(root)
            target_folder = target_repo_root
            if str(rel_dir) != ".":
                target_folder = (
                    f"{target_repo_root}/{_slugify_segment(str(rel_dir).replace('\\', '/'))}"
                )
            target = InfisicalTarget(
                project_id=project_id,
                environment=environment,
                folder=target_folder,
            )
            results.append(
                sync_env_file_to_infisical(
                    path,
                    target,
                    token=token,
                    brain=brain,
                    repo=repo or root.name,
                    branch=branch,
                    agent_id=agent_id,
                    origin=origin,
                )
            )
    return results


__all__ = [
    "NormalizedEnvFile",
    "SyncResult",
    "parse_env_text",
    "normalize_env_file",
    "sync_env_file_to_infisical",
    "sync_roots",
]
