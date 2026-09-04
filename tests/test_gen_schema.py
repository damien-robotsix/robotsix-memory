"""Unit tests for the schema generator (robotsix_memory.gen_schema).

``main()`` writes ``config/config.schema.json`` relative to the current
working directory. These tests chdir onto a tmp_path and assert the file is
written, parses as JSON, and carries the ``description`` key plus exactly the
``Settings`` property fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from robotsix_memory.config import Settings
from robotsix_memory.gen_schema import main


def test_main_writes_schema_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)

    main()

    out = tmp_path / "config" / "config.schema.json"
    assert out.is_file(), "config/config.schema.json should be written under the cwd"
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    assert schema["description"] == "Every setting robotsix-memory reads at runtime."
    assert set(schema["properties"]) == set(Settings.model_fields)


def test_main_overwrites_stale_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    stale = tmp_path / "config" / "config.schema.json"
    stale.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    main()

    schema = json.loads(stale.read_text(encoding="utf-8"))
    assert schema["description"] == "Every setting robotsix-memory reads at runtime."
