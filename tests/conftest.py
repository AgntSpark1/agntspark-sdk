"""Shared test isolation.

Tests must not be able to see the real machine's AGNTSPARK_* environment
variables or ~/.agntspark/config.yaml — a test asserting "no config
available" behavior (e.g. Client(api_key=None) raises) would otherwise
pass or fail depending on what's sitting on the developer's machine,
which is exactly the kind of leak that let a real Config.load() bug
(see test_config.py) go unnoticed.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_agntspark_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("AGNTSPARK_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGNTSPARK_CONFIG_FILE", str(tmp_path / "unused-config.yaml"))
