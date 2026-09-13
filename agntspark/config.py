"""
Configuration management for the AgntSpark SDK.

The SDK resolves configuration in the following order (later sources
override earlier ones):

1. Built-in defaults
2. ``~/.agntspark/config.yaml``  (or the path in ``AGNTSPARK_CONFIG_FILE``)
3. Environment variables prefixed with ``AGNTSPARK_``
4. Keyword arguments passed directly to :class:`Config` or :class:`Client`

Usage::

    from agntspark.config import Config

    cfg = Config.load()            # merge file + env
    cfg = Config.from_env()        # env only
    cfg = Config.from_file()       # file only
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path.home() / ".agntspark"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

DEFAULT_BASE_URL = "https://agntapi.agntspark.com/v1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 0.5
DEFAULT_RATE_LIMIT_RPM = 600  # requests per minute


@dataclass
class Config:
    """
    Resolved SDK configuration.

    Instances of this class are typically created via :meth:`load`,
    but can also be constructed manually when you need full control::

        cfg = Config(api_key="sk-…", base_url="https://staging.agntspark.com/v1")
    """

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    rate_limit_rpm: int = DEFAULT_RATE_LIMIT_RPM
    default_project: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, **overrides: Any) -> Config:
        """
        Build a :class:`Config` by merging file → env → kwargs.

        Keyword arguments take the highest priority.
        """
        merged: dict[str, Any] = {}

        # 1. File (lowest priority). from_file() only ever includes keys
        #    actually present in the YAML (see its own filtering), so every
        #    attribute on the returned Config that differs from "unset" is
        #    a real file value — safe to merge in wholesale via _merge_keys.
        file_cfg = cls.from_file()
        for attr in cls._merge_keys():
            value = getattr(file_cfg, attr)
            if value is not None:
                merged[attr] = value

        # 2. Environment (middle priority). Deliberately does NOT go
        #    through from_env(), which fills in class defaults for any
        #    unset var — comparing those defaulted values against None
        #    here couldn't tell "the env var is unset" from "the env var
        #    was set to the same value as the default", so a file-provided
        #    base_url/timeout/etc. would always be clobbered by from_env()'s
        #    defaults. Checking os.environ membership directly avoids that.
        merged.update(cls._explicit_env_overrides())

        # 3. Explicit overrides win — but only ones actually provided.
        #    Callers like Client() pass api_key=None as a *parameter
        #    default*, not a deliberate "erase this" instruction; treating
        #    None as "no override" is what lets ~/.agntspark/config.yaml /
        #    env vars work when Client() is called with no arguments at all.
        merged.update({k: v for k, v in overrides.items() if v is not None})

        return cls(**merged)

    @staticmethod
    def _explicit_env_overrides() -> dict[str, Any]:
        """Return only the config fields the user actually set via env vars."""
        env = os.environ
        overrides: dict[str, Any] = {}

        api_key = env.get("AGNTSPARK_API_KEY") or env.get("AGNTSPARK_API_TOKEN")
        if api_key is not None:
            overrides["api_key"] = api_key
        if "AGNTSPARK_BASE_URL" in env:
            overrides["base_url"] = env["AGNTSPARK_BASE_URL"]
        if "AGNTSPARK_TIMEOUT" in env:
            overrides["timeout"] = float(env["AGNTSPARK_TIMEOUT"])
        if "AGNTSPARK_MAX_RETRIES" in env:
            overrides["max_retries"] = int(env["AGNTSPARK_MAX_RETRIES"])
        if "AGNTSPARK_RETRY_BACKOFF" in env:
            overrides["retry_backoff"] = float(env["AGNTSPARK_RETRY_BACKOFF"])
        if "AGNTSPARK_RATE_LIMIT_RPM" in env:
            overrides["rate_limit_rpm"] = int(env["AGNTSPARK_RATE_LIMIT_RPM"])
        if "AGNTSPARK_PROJECT" in env:
            overrides["default_project"] = env["AGNTSPARK_PROJECT"]

        return overrides

    @classmethod
    def from_env(cls) -> Config:
        """Read configuration from ``AGNTSPARK_*`` environment variables."""
        env = os.environ

        api_key = env.get("AGNTSPARK_API_KEY") or env.get("AGNTSPARK_API_TOKEN")
        base_url = env.get("AGNTSPARK_BASE_URL", DEFAULT_BASE_URL)
        timeout = float(env.get("AGNTSPARK_TIMEOUT", DEFAULT_TIMEOUT))
        max_retries = int(env.get("AGNTSPARK_MAX_RETRIES", DEFAULT_MAX_RETRIES))
        retry_backoff = float(env.get("AGNTSPARK_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF))
        rate_limit_rpm = int(env.get("AGNTSPARK_RATE_LIMIT_RPM", DEFAULT_RATE_LIMIT_RPM))
        default_project = env.get("AGNTSPARK_PROJECT")

        return cls(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            rate_limit_rpm=rate_limit_rpm,
            default_project=default_project,
        )

    @classmethod
    def from_file(cls, path: Path | None = None) -> Config:
        """
        Read configuration from a YAML file.

        If *path* is not provided, the file at
        ``$AGNTSPARK_CONFIG_FILE`` or ``~/.agntspark/config.yaml``
        is used.  If the file does not exist, a :class:`Config` with all
        defaults is returned.
        """
        path = path or Path(os.environ.get("AGNTSPARK_CONFIG_FILE", CONFIG_FILE))
        if not path.exists():
            return cls()

        with open(path) as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}

        known = cls._merge_keys()
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    # ------------------------------------------------------------------
    # File writing
    # ------------------------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        """
        Persist the current configuration to YAML.

        Parameters
        ----------
        path:
            Destination file path.  Defaults to ``~/.agntspark/config.yaml``.

        Returns
        -------
        Path
            The path the config was written to.
        """
        path = path or Path(os.environ.get("AGNTSPARK_CONFIG_FILE", CONFIG_FILE))
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "retry_backoff": self.retry_backoff,
            "rate_limit_rpm": self.rate_limit_rpm,
            "default_project": self.default_project,
        }
        with open(path, "w") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)
        return path

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """``True`` when an API key has been set."""
        return bool(self.api_key)

    @staticmethod
    def _merge_keys() -> tuple:
        return (
            "api_key",
            "base_url",
            "timeout",
            "max_retries",
            "retry_backoff",
            "rate_limit_rpm",
            "default_project",
            "extra_headers",
        )


__all__ = ["Config", "CONFIG_DIR", "CONFIG_FILE"]
