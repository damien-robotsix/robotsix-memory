"""Runtime settings for the robotsix-memory wrapper.

Settings are a :class:`robotsix_config.ConfigModel` loaded from the
fleet-standard single JSON config file (path in ``ROBOTSIX_CONFIG_FILE``,
default ``config/config.json``) via :func:`robotsix_config.load_config` — one
pydantic model, one JSON file, versioned history. The deploy plane owns the
config volume; the schema in ``config/config.schema.json`` is generated from
this model and must be regenerated when fields change
(``python -m robotsix_memory.gen_schema``).

The config file is the single source of config values (per the
config-ownership contract). The one kept deploy-time env override is
``MEMORY_HINDSIGHT_URL``, which points at the sibling Hindsight engine's
endpoint and can vary per deployment.
"""

from __future__ import annotations

import os

from robotsix_config import ConfigModel, load_config


class Settings(ConfigModel):
    """Every setting robotsix-memory reads at runtime."""

    hindsight_url: str = "http://memory-hindsight:8888"
    request_timeout: float = 60.0
    retain_timeout: float = 120.0
    recall_limit: int = 10
    bank_prefix: str = "fleet"
    log_level: str = "INFO"


def load_settings() -> Settings:
    """Build settings: the JSON config file is the single source of values.

    ``MEMORY_HINDSIGHT_URL`` is the one kept env-prefix override — the
    sibling Hindsight engine's endpoint genuinely varies per deployment, so
    it wins over the file value.
    """
    settings = load_config(Settings)
    hindsight_url = os.environ.get("MEMORY_HINDSIGHT_URL")
    if hindsight_url:
        settings = settings.model_copy(update={"hindsight_url": hindsight_url})
    return settings
