"""Unit tests for the runtime settings loader (robotsix_memory.config).

Settings load through the fleet-standard ``robotsix_config.load_config``: the
JSON config file (path in ``ROBOTSIX_CONFIG_FILE``) is the single source of
values, and the model's own field defaults fill anything the file omits.
``load_settings()`` is checked for defaults, file values as the base,
``InvalidConfigError`` on malformed/non-dict input, and the single kept
deploy-time override (``MEMORY_HINDSIGHT_URL`` → ``hindsight_url``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from robotsix_config import InvalidConfigError

from robotsix_memory.config import load_settings


def test_load_settings_defaults_without_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(tmp_path / "does-not-exist.json"))

    settings = load_settings()

    assert settings.hindsight_url == "http://memory-hindsight:8888"
    assert settings.request_timeout == 60.0
    assert settings.retain_timeout == 120.0
    assert settings.recall_limit == 10
    assert settings.bank_prefix == "fleet"
    assert settings.log_level == "INFO"


def test_load_settings_uses_file_values_as_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"recall_limit": 3, "bank_prefix": "unit"}), encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))

    settings = load_settings()

    assert settings.recall_limit == 3
    assert settings.bank_prefix == "unit"
    assert settings.hindsight_url == "http://memory-hindsight:8888"


def test_load_settings_malformed_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text('{"hindsight_url": ', encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))

    with pytest.raises(InvalidConfigError):
        load_settings()


def test_load_settings_non_dict_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))

    with pytest.raises(InvalidConfigError):
        load_settings()


def test_load_settings_env_var_overrides_default_hindsight_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("MEMORY_HINDSIGHT_URL", "http://other-hindsight:9999")

    settings = load_settings()

    assert settings.hindsight_url == "http://other-hindsight:9999"


def test_load_settings_env_var_wins_over_file_for_hindsight_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hindsight_url": "http://file-hindsight:8888"}), encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("MEMORY_HINDSIGHT_URL", "http://env-hindsight:9999")

    settings = load_settings()

    assert settings.hindsight_url == "http://env-hindsight:9999"


def test_load_settings_other_env_vars_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only hindsight_url keeps an env override; the file stays the source for the rest."""
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("MEMORY_RECALL_LIMIT", "7")

    settings = load_settings()

    assert settings.recall_limit == 10
