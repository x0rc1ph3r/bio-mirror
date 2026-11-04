"""Reproducibility metadata for verification reports."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__


def hash_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(file_path.resolve()),
        "sha256": digest.hexdigest(),
        "size_bytes": file_path.stat().st_size,
    }


def _git_metadata(repo: str | Path) -> dict[str, Any]:
    repo_path = Path(repo).resolve()
    result: dict[str, Any] = {"path": str(repo_path)}
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        dirty_output = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        result.update({"head": head, "dirty": bool(dirty_output.strip())})
    except (OSError, subprocess.SubprocessError) as exc:
        result["unavailable"] = str(exc)
    return result


def collect_provenance(
    *,
    reference: str | Path | None = None,
    actual: str | Path | None = None,
    contract: str | Path | None = None,
    firmware: str | Path | None = None,
    sim_config: str | Path | None = None,
    repo: str | Path | None = None,
    container_image: str | None = None,
) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for name, path in (
        ("reference", reference),
        ("actual", actual),
        ("contract", contract),
        ("firmware", firmware),
        ("sim_config", sim_config),
    ):
        if path is not None:
            files[name] = hash_file(path)

    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": {"name": "BIO-Mirror", "version": __version__},
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": sys.platform,
        },
        "files": files,
    }
    if repo is not None:
        result["git"] = _git_metadata(repo)
    if container_image is not None:
        result["container_image"] = container_image
    return result
