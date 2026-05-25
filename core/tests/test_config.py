"""Layered credential merge: file → file → env → explicit. Later wins.
Also covers ``discover_env_var`` non-procfs path (linux-only paths
are platform-skipped)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from clawtell_core.config import discover_env_var, load_credentials


def _write_env(path: Path, **kv: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f'{k}="{v}"' for k, v in kv.items()),
        encoding="utf-8",
    )


def test_explicit_arg_overrides_env_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if sys.platform.startswith("win"):
        monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAWTELL_BASELINE_ENV", raising=False)
    _write_env(home / ".clawtell" / "credentials.env", CLAWTELL_API_KEY="from-file")
    monkeypatch.setenv("CLAWTELL_API_KEY", "from-env")
    creds = load_credentials(api_key="from-arg")
    assert creds.api_key == "from-arg"
    assert "explicit:api_key" in creds.source


def test_env_overrides_file_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if sys.platform.startswith("win"):
        monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAWTELL_BASELINE_ENV", raising=False)
    _write_env(home / ".clawtell" / "credentials.env", CLAWTELL_API_KEY="from-file")
    monkeypatch.setenv("CLAWTELL_API_KEY", "from-env")
    creds = load_credentials()
    assert creds.api_key == "from-env"


def test_later_file_overrides_earlier_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``~/.config/clawtell.env`` is loaded AFTER
    ``~/.clawtell/credentials.env`` per the candidate-path order, so a
    duplicate key in the later file wins."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if sys.platform.startswith("win"):
        monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAWTELL_BASELINE_ENV", raising=False)
    monkeypatch.delenv("CLAWTELL_API_KEY", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    _write_env(home / ".clawtell" / "credentials.env", CLAWTELL_API_KEY="A")
    _write_env(home / ".config" / "clawtell.env", CLAWTELL_API_KEY="B")
    creds = load_credentials()
    assert creds.api_key == "B"


def test_baseline_env_is_lowest_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Baseline (e.g. /opt/data/.env on the user's image) provides a
    floor; sidecar files override it."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if sys.platform.startswith("win"):
        monkeypatch.setenv("USERPROFILE", str(home))
    baseline = tmp_path / "baseline.env"
    _write_env(baseline, CLAWTELL_API_KEY="baseline", CLAWTELL_NAME="bot")
    monkeypatch.setenv("CLAWTELL_BASELINE_ENV", str(baseline))
    _write_env(home / ".clawtell" / "credentials.env", CLAWTELL_API_KEY="sidecar")
    monkeypatch.delenv("CLAWTELL_API_KEY", raising=False)
    monkeypatch.delenv("CLAWTELL_NAME", raising=False)
    creds = load_credentials()
    assert creds.api_key == "sidecar"  # sidecar overrides baseline
    assert creds.name == "bot"         # baseline provides the field sidecar omits


def test_discover_env_var_reads_process_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CT_TEST_TOKEN", "abc123")
    assert discover_env_var("CT_TEST_TOKEN") == "abc123"


def test_discover_env_var_missing_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CT_TEST_TOKEN", raising=False)
    assert discover_env_var("CT_TEST_TOKEN", also_check_procfs=False) is None
