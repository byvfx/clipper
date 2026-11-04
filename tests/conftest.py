"""Shared fixtures. Tests are offline: no network, ffmpeg, GPU or model calls."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from clipper import config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No CLIPPER_* setting from this machine leaks into a test, nor a stream's channel
    cached by an earlier one."""
    for name in list(os.environ):
        if name.startswith("CLIPPER_"):
            monkeypatch.delenv(name)
    config._CHANNELS.clear()


@pytest.fixture
def defaults(tmp_path):
    """The built-in defaults, as `clipper` loads them with no clipper.toml; people.json (which
    story and investigate read) in tmp_path, never this checkout's own."""
    cfg = config.load(tmp_path / "clipper.toml", use_local=False)
    cfg["paths"]["people"] = tmp_path / "people.json"
    return cfg
