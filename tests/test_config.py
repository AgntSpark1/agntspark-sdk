"""
Unit tests for Config's file/env/override merge priority.

Regression coverage for a real bug: Config.load() used to compare
already-defaulted Config instances (from from_env()/explicit kwargs) to
decide what "wasn't set" meant, which meant any field with a non-None
default (base_url, timeout, ...) always clobbered a value loaded from
~/.agntspark/config.yaml, and Client()'s own api_key=None default
parameter clobbered a file-loaded api_key too — silently breaking the
entire config-file feature for everything except default_project.
"""

from __future__ import annotations

import os

import pytest
import yaml

from agntspark.config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, Config


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """Ensure no AGNTSPARK_* env vars — and no real ~/.agntspark/config.yaml
    on the machine running the tests — leak into a test."""
    for key in list(os.environ):
        if key.startswith("AGNTSPARK_"):
            monkeypatch.delenv(key, raising=False)
    # Redirect to a path that doesn't exist unless a test creates it via
    # the config_file fixture below.
    monkeypatch.setenv("AGNTSPARK_CONFIG_FILE", str(tmp_path / "unused-config.yaml"))


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("AGNTSPARK_CONFIG_FILE", str(path))

    def _write(data: dict) -> None:
        with open(path, "w") as fh:
            yaml.safe_dump(data, fh)

    return _write


def test_load_with_no_sources_uses_defaults(clean_env) -> None:
    cfg = Config.load()
    assert cfg.api_key is None
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.timeout == DEFAULT_TIMEOUT


def test_load_picks_up_api_key_from_file(clean_env, config_file) -> None:
    config_file({"api_key": "agnt_from_file"})
    cfg = Config.load()
    assert cfg.api_key == "agnt_from_file"


def test_load_picks_up_base_url_from_file(clean_env, config_file) -> None:
    """Regression: base_url from the file used to always lose to
    from_env()'s DEFAULT_BASE_URL fallback."""
    config_file({"base_url": "http://localhost:8899/v1"})
    cfg = Config.load()
    assert cfg.base_url == "http://localhost:8899/v1"


def test_load_picks_up_multiple_file_fields(clean_env, config_file) -> None:
    config_file(
        {
            "api_key": "agnt_from_file",
            "base_url": "http://localhost:8899/v1",
            "timeout": 45.0,
            "max_retries": 7,
        }
    )
    cfg = Config.load()
    assert cfg.api_key == "agnt_from_file"
    assert cfg.base_url == "http://localhost:8899/v1"
    assert cfg.timeout == 45.0
    assert cfg.max_retries == 7


def test_env_var_overrides_file(clean_env, config_file, monkeypatch) -> None:
    config_file({"base_url": "http://localhost:8899/v1"})
    monkeypatch.setenv("AGNTSPARK_BASE_URL", "http://from-env:9000/v1")
    cfg = Config.load()
    assert cfg.base_url == "http://from-env:9000/v1"


def test_explicit_kwarg_overrides_file_and_env(clean_env, config_file, monkeypatch) -> None:
    config_file({"base_url": "http://localhost:8899/v1"})
    monkeypatch.setenv("AGNTSPARK_BASE_URL", "http://from-env:9000/v1")
    cfg = Config.load(base_url="http://explicit:1234/v1")
    assert cfg.base_url == "http://explicit:1234/v1"


def test_none_kwarg_does_not_clobber_file_value(clean_env, config_file) -> None:
    """Regression: Client() passes api_key=None as a parameter default —
    that must NOT erase a file-loaded api_key."""
    config_file({"api_key": "agnt_from_file"})
    cfg = Config.load(api_key=None)
    assert cfg.api_key == "agnt_from_file"


def test_client_with_no_args_uses_file_config(clean_env, config_file) -> None:
    from agntspark import Client

    config_file({"api_key": "agnt_from_file", "base_url": "http://localhost:8899/v1"})
    client = Client()
    assert client._config.api_key == "agnt_from_file"
    assert client._config.base_url == "http://localhost:8899/v1"
    client.close()
