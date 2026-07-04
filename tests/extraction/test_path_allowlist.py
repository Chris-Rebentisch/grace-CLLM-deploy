"""Unit tests for source-path allowlist validation (D470, CWE-22 defense)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException


def test_path_resolve_success(tmp_path):
    """Valid path within allowed roots resolves successfully."""
    from src.api.extraction_routes import _validate_source_path

    # Create a temp file inside a temp dir that we add to allowed roots
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]):
        result = _validate_source_path(str(test_file))
        assert result == test_file.resolve()


def test_symlink_outside_roots_rejected(tmp_path):
    """Symlink whose resolved target falls outside allowed roots is rejected."""
    from src.api.extraction_routes import _validate_source_path

    # Create a real file outside allowed roots
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    target = outside_dir / "secret.txt"
    target.write_text("secret")

    # Create a symlink inside allowed area pointing to the outside file
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    link = allowed_dir / "link.txt"
    link.symlink_to(target)

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[allowed_dir]):
        with pytest.raises(HTTPException) as exc_info:
            _validate_source_path(str(link))
        assert exc_info.value.status_code == 422


def test_traversal_rejected(tmp_path):
    """Path with ../ traversal outside allowed roots is rejected."""
    from src.api.extraction_routes import _validate_source_path

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()

    # Create a file outside the allowed dir
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")

    traversal_path = str(allowed_dir / ".." / "secret.txt")

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[allowed_dir]):
        with pytest.raises(HTTPException) as exc_info:
            _validate_source_path(traversal_path)
        assert exc_info.value.status_code == 422
