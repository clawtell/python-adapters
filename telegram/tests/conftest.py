"""Route ``$CLAWTELL_HOME`` to a tmp dir for bridge persistence tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_clawtell_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "clawtell"
    home.mkdir()
    monkeypatch.setenv("CLAWTELL_HOME", str(home))
    return home
