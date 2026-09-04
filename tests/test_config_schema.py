"""Guard that ``config/config.schema.json`` stays in sync with Settings.

The committed schema is a generated artifact (see
``robotsix_memory.gen_schema``). This test regenerates it exactly as
``gen_schema.main()`` does and asserts byte-for-byte equality, so any
added/removed/renamed setting that is not regenerated fails CI instead
of silently drifting the deployed schema.
"""

from __future__ import annotations

import json
from pathlib import Path

from robotsix_memory.config import Settings


def test_committed_schema_matches_settings() -> None:
    schema = Settings.model_json_schema()
    schema["description"] = "Every setting robotsix-memory reads at runtime."
    expected = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    committed = Path("config/config.schema.json").read_text(encoding="utf-8")

    assert committed == expected, (
        "config/config.schema.json is out of sync with Settings; "
        "regenerate it with `uv run python -m robotsix_memory.gen_schema`."
    )
