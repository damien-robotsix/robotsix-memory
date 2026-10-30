"""Guard that ``docs/configuration.md`` stays in sync with Settings.

The configuration reference table must list every ``Settings`` field, in
declaration order, with its annotated type and default value, so that an
added/removed/renamed setting (or a changed default) fails CI instead of
silently drifting the documentation.
"""

from __future__ import annotations

import json
from pathlib import Path

from robotsix_memory.config import Settings

DOC = Path("docs/configuration.md")


def _table_rows() -> list[list[str]]:
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append([cell.strip("`") for cell in cells])
    return rows


def test_config_doc_lists_every_settings_field() -> None:
    rows = _table_rows()
    assert [row[0] for row in rows] == list(Settings.model_fields), (
        "docs/configuration.md is out of sync with Settings.model_fields; "
        "add, remove or reorder rows to match."
    )


def test_config_doc_types_and_defaults_match() -> None:
    for name, type_, default, *_ in _table_rows():
        field = Settings.model_fields[name]
        assert field.annotation is not None
        assert type_ == field.annotation.__name__, name
        assert default == json.dumps(field.default), name


def test_config_doc_env_overrides() -> None:
    overrides = {row[0]: row[4] for row in _table_rows()}
    assert overrides.pop("hindsight_url") == "MEMORY_HINDSIGHT_URL"
    assert set(overrides.values()) == {"none"}
