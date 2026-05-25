"""Credential discovery for image-baked agents.

Walks a deterministic list of locations so a `ClawTell()` no-arg
constructor (or our daemon entry point) just works without per-image
plumbing. Mirrors the user's manual setup on Hermes — including the
realistic case where an image's read-only baseline ``.env`` provides
SOME credentials and a writable sidecar adds/overrides others.

Lookup is layered (later overrides earlier), then env, then explicit args:
  1. ``CLAWTELL_BASELINE_ENV`` (default ``/opt/data/.env`` on linux, else skipped)
  2. ``~/.clawtell/credentials.env``
  3. ``~/.config/clawtell.env``
  4. ``$XDG_CONFIG_HOME/clawtell/credentials.env``
  5. environment variables (``CLAWTELL_API_KEY`` / ``CLAWTELL_NAME``)
  6. explicit constructor args
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Credentials:
    api_key: Optional[str]
    name: Optional[str]
    source: str  # which path / env we read from, for debugging


def _candidate_paths() -> list[Path]:
    """Earliest-first; later entries override fields from earlier ones."""
    paths: list[Path] = []

    baseline = os.environ.get("CLAWTELL_BASELINE_ENV")
    if baseline:
        paths.append(Path(baseline))
    elif Path("/opt/data/.env").exists():
        paths.append(Path("/opt/data/.env"))

    home = Path.home()
    paths.append(home / ".clawtell" / "credentials.env")
    paths.append(home / ".config" / "clawtell.env")

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        paths.append(Path(xdg) / "clawtell" / "credentials.env")

    return paths


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            out[k.strip()] = v
    except FileNotFoundError:
        return {}
    except OSError as e:
        log.debug("could not read %s: %s", path, e)
        return {}
    return out


def load_credentials(
    *, api_key: Optional[str] = None, name: Optional[str] = None
) -> Credentials:
    """Resolve credentials. Layered merge: files (earliest-first) →
    overridden by env vars → overridden by explicit args."""
    merged: dict[str, str] = {}
    sources: list[str] = []

    for path in _candidate_paths():
        kv = _parse_env_file(path)
        if kv:
            merged.update(kv)
            sources.append(str(path))

    env_key = os.environ.get("CLAWTELL_API_KEY")
    env_name = os.environ.get("CLAWTELL_NAME")
    if env_key:
        merged["CLAWTELL_API_KEY"] = env_key
        sources.append("env:CLAWTELL_API_KEY")
    if env_name:
        merged["CLAWTELL_NAME"] = env_name
        sources.append("env:CLAWTELL_NAME")

    if api_key:
        merged["CLAWTELL_API_KEY"] = api_key
        sources.append("explicit:api_key")
    if name:
        merged["CLAWTELL_NAME"] = name
        sources.append("explicit:name")

    return Credentials(
        api_key=merged.get("CLAWTELL_API_KEY"),
        name=merged.get("CLAWTELL_NAME") or name,
        source=" + ".join(sources) if sources else "none",
    )


def discover_env_var(
    name: str, *, also_check_procfs: bool = True
) -> Optional[str]:
    """Find an env var that may have been loaded by a parent process but
    not inherited by this one. Used for ``TELEGRAM_BOT_TOKEN`` on
    image-baked agents where the gateway loaded the token but the worker
    process didn't inherit it.

    Order: own env → /proc/self/environ → /proc/1/environ (linux only).
    """
    if name in os.environ:
        return os.environ[name]
    if not also_check_procfs:
        return None
    import sys

    if not sys.platform.startswith("linux"):
        return None
    for procfs_path in ("/proc/self/environ", "/proc/1/environ"):
        try:
            with open(procfs_path, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        for raw in blob.split(b"\x00"):
            if not raw:
                continue
            try:
                k, _, v = raw.decode("utf-8", "replace").partition("=")
            except UnicodeDecodeError:
                continue
            if k == name:
                return v
    return None
