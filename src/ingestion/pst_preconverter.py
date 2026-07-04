"""PstPreconverter — subprocess boundary for readpst (GPL isolation).

Chunk 55, D419. Calls ``readpst -r -o`` via ``subprocess.run()`` with an
explicit argument list — no ``shell=True`` (security-posture §39.1).

Output directory: ``<converted_output_dir>/<source_id>/`` — the base directory
comes from ``PstSourceConfig.converted_output_dir``; this module appends
``<source_id>/`` at runtime.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import UUID

import structlog

logger = structlog.get_logger()


class PstPreconverter:
    """Converts a .pst file to mbox format using the ``readpst`` CLI."""

    def __init__(self, pst_path: str, source_id: UUID, converted_output_dir: str) -> None:
        self.pst_path = Path(pst_path)
        self.source_id = source_id
        self.out_dir = Path(converted_output_dir) / str(source_id)

    def convert(self) -> Path:
        """Run readpst and return the output directory.

        Raises:
            RuntimeError: if ``readpst`` is not on ``$PATH`` or conversion fails.
        """
        if shutil.which("readpst") is None:
            raise RuntimeError(
                "readpst not found on $PATH. "
                "Install libpst: brew install libpst (macOS) or "
                "apt-get install pst-utils (Debian/Ubuntu)."
            )

        # Disk-space precheck
        if self.pst_path.exists():
            pst_size = self.pst_path.stat().st_size
            disk = shutil.disk_usage(self.pst_path.parent)
            if disk.free < 2 * pst_size:
                logger.warning(
                    "pst_preconverter_disk_low",
                    free_bytes=disk.free,
                    pst_bytes=pst_size,
                    msg="Free disk space is less than 2× PST file size.",
                )

        self.out_dir.mkdir(parents=True, exist_ok=True)

        # Explicit argument list — no shell=True (security-posture §39.1)
        result = subprocess.run(
            ["readpst", "-r", "-o", str(self.out_dir), str(self.pst_path)],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"readpst failed (exit {result.returncode}): {result.stderr.strip()}"
            )

        logger.info(
            "pst_preconverter_complete",
            source_id=str(self.source_id),
            output_dir=str(self.out_dir),
        )
        return self.out_dir
