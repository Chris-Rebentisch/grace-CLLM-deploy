"""D455: Assert ArcadeDB Docker Compose port mappings bind loopback only."""

import re
from pathlib import Path

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.arcade.yml"


def test_port_mappings_bind_loopback():
    text = COMPOSE_PATH.read_text()
    # Match port mapping lines (e.g. - "127.0.0.1:2480:2480")
    port_lines = re.findall(r'-\s*"([^"]+)"', text)
    # Filter to the two ArcadeDB port mappings
    arcade_ports = [p for p in port_lines if "2480" in p or "2424" in p]
    assert len(arcade_ports) == 2, f"Expected 2 ArcadeDB port mappings, found {len(arcade_ports)}"
    for mapping in arcade_ports:
        assert mapping.startswith("127.0.0.1:"), (
            f"Port mapping '{mapping}' does not bind loopback (127.0.0.1:) — "
            "D138 airgap-default requires loopback-only binding (D455)"
        )
