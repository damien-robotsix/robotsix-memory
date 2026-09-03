"""Regenerate ``config/config.schema.json`` from the Settings model.

Run after changing :class:`robotsix_memory.config.Settings`::

    uv run python -m robotsix_memory.gen_schema
"""

from __future__ import annotations

import json
from pathlib import Path

from robotsix_memory.config import Settings


def main() -> None:
    schema = Settings.model_json_schema()
    schema["description"] = "Every setting robotsix-memory reads at runtime."
    out = Path("config/config.schema.json")
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
