"""Unit tests for the runtime settings loader (robotsix_memory.config).

``_config_file_values()`` is exercised directly with ``ROBOTSIX_CONFIG_FILE``
pointing at tmp_path fixtures, covering each branch: missing file, OSError /
ValueError on read-or-parse, non-dict JSON, and a valid dict. ``load_settings()``
is checked for defaults, file values as the base, and ``MEMORY_``-prefixed env
vars filling fields the file omits (pydantic-settings gives init kwargs — the
file values — precedence over env vars, so env cannot override a
file-provided field despite the load_settings docstring phrasing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from robotsix_memory.config import _config_file_values, load_settings


def test_config_file_values_missing_file_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(tmp_path / "absent.json"))

    assert _config_file_values() == {}


def test_config_file_values_malformed_json_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text('{"hindsight_url": ', encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))

    assert _config_file_values() == {}


def test_config_file_values_read_oserror_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))

    def _raise_oserror(*_args: object, **_kwargs: object) -> object:
        raise OSError("simulated read failure")

    monkeypatch.setattr(json, "loads", _raise_oserror)

    assert _config_file_values() == {}


def test_config_file_values_non_dict_json_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))

    assert _config_file_values() == {}


def test_config_file_values_valid_dict_returned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"recall_limit": 3, "log_level": "DEBUG"}), encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))

    assert _config_file_values() == {"recall_limit": 3, "log_level": "DEBUG"}


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


def test_load_settings_env_var_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("MEMORY_RECALL_LIMIT", "7")

    settings = load_settings()

    assert settings.recall_limit == 7


def test_load_settings_env_var_fills_field_file_omits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"recall_limit": 3}), encoding="utf-8")
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("MEMORY_LOG_LEVEL", "DEBUG")

    settings = load_settings()

    assert settings.recall_limit == 3  # file-provided field keeps the file value
    assert settings.log_level == "DEBUG"  # env fills the omitted field
