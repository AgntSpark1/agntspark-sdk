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
from typing import Any, Dict, Optional

import yaml


CONFIG_DIR = Path.home() / ".agntspark"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

DEFAULT_BASE_URL = "https://api.agntspark.io/v1"
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

        cfg = Config(api_key="sk-…", base_url="https://staging.agntspark.io/v1")
    """

    api_key: Optional[str] = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    rate_limit_rpm: int = DEFAULT_RATE_LIMIT_RPM
    default_project: Optional[str] = None
    extra_headers: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, **overrides: Any) -> "Config":
        """
        Build a :class:`Config` by merging file → env → kwargs.

        Keyword arguments take the highest priority.
        """
        file_cfg = cls.from_file()
        env_cfg = cls.from_env()

        merged: Dict[str, Any] = {}
        # file first (lowest priority among the two automatic sources)
        for attr in cls._merge_keys():
            if getattr(file_cfg, attr) is not None:
                merged[attr] = getattr(file_cfg, attr)
            if getattr(env_cfg, attr) is not None:
                merged[attr] = getattr(env_cfg, attr)
        # explicit overrides win
        merged.update(overrides)

        return cls(**merged)

    @classmethod
    def from_env(cls) -> "Config":
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
    def from_file(cls, path: Optional[Path] = None) -> "Config":
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

        with open(path, "r") as fh:
            data: Dict[str, Any] = yaml.safe_load(fh) or {}

        known = cls._merge_keys()
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    # ------------------------------------------------------------------
    # File writing
    # ------------------------------------------------------------------

    def save(self, path: Optional[Path] = None) -> Path:
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
        data: Dict[str, Any] = {
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
