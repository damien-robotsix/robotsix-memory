"""Runtime settings for the robotsix-memory wrapper.

Settings load from a JSON config file (path in ``ROBOTSIX_CONFIG_FILE``,
default ``config/config.json``) with environment-variable overrides
(``MEMORY_``-prefixed). The deploy plane owns the config volume; the schema
in ``config/config.schema.json`` is generated from this model and must be
regenerated when fields change (``python -m robotsix_memory.gen_schema``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


def _config_file_values() -> dict[str, Any]:
    """Read the JSON config file if present; missing file means defaults."""
    path = Path(os.environ.get("ROBOTSIX_CONFIG_FILE", "config/config.json"))
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


class Settings(BaseSettings):
    """Every setting robotsix-memory reads at runtime."""

    model_config = SettingsConfigDict(env_prefix="MEMORY_")

    hindsight_url: str = "http://memory-hindsight:8888"
    request_timeout: float = 60.0
    retain_timeout: float = 120.0
    recall_limit: int = 10
    bank_prefix: str = "fleet"
    log_level: str = "INFO"


def load_settings() -> Settings:
    """Build settings: config-file values take precedence.

    File values are passed as pydantic-settings init kwargs, which
    outrank ``MEMORY_``-prefixed environment variables. An env var
    therefore only fills a field the config file omits; it cannot
    override a field the file already sets.
    """
    return Settings(**_config_file_values())
