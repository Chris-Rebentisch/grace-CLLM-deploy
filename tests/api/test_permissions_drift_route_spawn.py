"""D478 + D476: Regression test for drift-detection route subprocess spawn.

Monkeypatches ``subprocess.Popen`` in ``src.api.permissions_routes`` and
asserts the spawned module path is ``src.permissions.drift_detector``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.api.permissions_routes import _spawn_permissions_cli


def test_drift_spawn_uses_drift_detector_module():
    """D478 regression (R5-H1): drift route must spawn src.permissions.drift_detector."""
    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        return mock_proc

    with patch("src.api.permissions_routes.subprocess.Popen", side_effect=fake_popen):
        pid = _spawn_permissions_cli([
            "drift", "run",
            "--observation-time", "2026-05-27T12:00:00+00:00",
            "--job-id", "00000000-0000-0000-0000-000000000001",
        ])

    assert pid == 12345
    # The spawned command must use the drift_detector module, not src.permissions.cli
    assert "src.permissions.drift_detector" in captured_cmd, (
        f"Expected src.permissions.drift_detector in spawned cmd, got: {captured_cmd}"
    )
    # "drift" subcommand token should be stripped — argv should contain "run", not "drift"
    # (the module handles subcommand dispatch internally)
    module_idx = captured_cmd.index("src.permissions.drift_detector")
    remainder = captured_cmd[module_idx + 1:]
    assert "drift" not in remainder, (
        f"'drift' token should be stripped from argv tail, got: {remainder}"
    )
    assert "run" in remainder
