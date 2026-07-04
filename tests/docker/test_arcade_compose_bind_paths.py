"""D478: Regression test — docker-compose.arcade.yml bind-mount paths.

Parses ``docker/docker-compose.arcade.yml`` via ``yaml.safe_load()`` and
validates that bind-mount source paths use ``/home/arcadedb/databases``
as the container target (not ``/opt/arcadedb/databases``).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


_COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.arcade.yml"


def test_arcade_compose_bind_mount_targets_home():
    """D478 regression: ArcadeDB bind-mount must target /home/arcadedb/databases."""
    assert _COMPOSE_PATH.exists(), f"docker-compose.arcade.yml not found at {_COMPOSE_PATH}"
    data = yaml.safe_load(_COMPOSE_PATH.read_text())
    assert isinstance(data, dict)

    services = data.get("services", {})
    assert "arcadedb" in services, "arcadedb service not found in compose file"

    volumes = services["arcadedb"].get("volumes", [])
    assert len(volumes) > 0, "No volumes defined for arcadedb service"

    # Check each bind-mount: container target must be /home/arcadedb/databases
    for vol in volumes:
        if isinstance(vol, str) and ":" in vol:
            parts = vol.split(":")
            container_path = parts[-1]  # last segment after colon
            if "arcadedb" in container_path and "databases" in container_path:
                assert "/home/arcadedb/databases" in container_path, (
                    f"Bind-mount target must be /home/arcadedb/databases, "
                    f"got {container_path}"
                )
                # Source path should be expandable
                source_path = parts[0]
                expanded = os.path.expanduser(os.path.expandvars(source_path))
                assert expanded, "Source path should be non-empty after expansion"
