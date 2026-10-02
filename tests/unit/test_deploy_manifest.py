"""Guard tests linking the deploy manifest to log-rendering behaviour.

:func:`robotsix_memory.logging_config.configure_logging` emits JSON only when
``ENVIRONMENT=production`` is set in the running container. Nothing in the code
enforces that the deploy manifest actually sets it, so a drift there silently
reverts production logs to the console renderer. These tests assert the deploy
manifest keeps the contract.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = _REPO_ROOT / "deploy" / "docker-compose.yml"


def test_deploy_manifest_sets_environment_production() -> None:
    manifest = yaml.safe_load(_COMPOSE_FILE.read_text(encoding="utf-8"))
    env = manifest["services"]["memory"]["environment"]
    assert env.get("ENVIRONMENT") == "production", (
        "deploy/docker-compose.yml must set ENVIRONMENT=production on the "
        "memory service so configure_logging emits JSON for log aggregation."
    )
