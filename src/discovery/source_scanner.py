"""Filesystem scanner for onboarding source selector UI."""

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog

from src.discovery.models import load_discovery_config

logger = structlog.get_logger()

# Directories to skip during scanning
_SKIP_DIRS = {"Library", "Applications", ".Trash"}

# Directories that default to suggested_include=True
_SUGGESTED_DIRS = {"Documents", "Desktop", "Downloads"}


def scan_sources(root_dir: Path | None = None) -> list[dict]:
    """Scan top-level directories under the user's home directory (or root_dir).

    Returns a list of dicts, one per directory, with file counts and sizes.
    Hidden directories and system directories (Library, Applications, .Trash)
    are skipped.
    """
    if root_dir is None:
        root_dir = Path.home()
    root_dir = Path(root_dir)

    config = load_discovery_config()
    supported_extensions = set(config["supported_extensions"])

    results = []
    try:
        entries = sorted(root_dir.iterdir())
    except PermissionError:
        logger.warning("scan_sources_permission_denied", path=str(root_dir))
        return []

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue

        total_files = 0
        document_files = 0
        total_size = 0
        document_size = 0

        try:
            for f in entry.rglob("*"):
                if not f.is_file():
                    continue
                total_files += 1
                try:
                    size = f.stat().st_size
                except OSError:
                    size = 0
                total_size += size
                if f.suffix.lower() in supported_extensions:
                    document_files += 1
                    document_size += size
        except PermissionError:
            logger.warning("scan_sources_permission_denied", path=str(entry))

        results.append({
            "name": entry.name,
            "path": str(entry),
            "total_files": total_files,
            "document_files": document_files,
            "total_size_bytes": total_size,
            "document_size_bytes": document_size,
            "suggested_include": entry.name in _SUGGESTED_DIRS,
        })

    return results


def browse_path(path: str | None = None) -> dict:
    """List the immediate folders and files under ``path`` for the file browser.

    Powers the in-app navigable file browser: the operator drills down folder
    by folder and selects exact folders or individual files (Option B). Cheap by
    design — only the direct children are stat-ed, never a recursive walk.
    Hidden entries and the system dirs in ``_SKIP_DIRS`` are omitted. Files are
    flagged ``supported`` when their extension is in the discovery config so the
    UI can offer (or disable) the per-file checkbox.
    """
    target = Path(path).expanduser() if path else Path.home()
    try:
        target = target.resolve()
    except (OSError, RuntimeError):
        return {"path": str(target), "parent": None, "error": "invalid path", "entries": []}

    if not target.is_dir():
        return {
            "path": str(target),
            "parent": str(target.parent),
            "error": "not a directory",
            "entries": [],
        }

    config = load_discovery_config()
    supported = set(config["supported_extensions"])

    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        logger.warning("browse_path_permission_denied", path=str(target))
        return {
            "path": str(target),
            "parent": str(target.parent),
            "error": "permission denied",
            "entries": [],
        }

    entries: list[dict] = []
    for child in children:
        if child.name.startswith("."):
            continue
        is_dir = child.is_dir()
        if is_dir and child.name in _SKIP_DIRS:
            continue
        size = 0
        is_supported = False
        if not is_dir:
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            is_supported = child.suffix.lower() in supported
        entries.append({
            "name": child.name,
            "path": str(child),
            "is_dir": is_dir,
            "size_bytes": size,
            "supported": is_supported,
        })

    # ``parent`` is None only at a filesystem root (where parent == self).
    parent = None if target.parent == target else str(target.parent)
    return {"path": str(target), "parent": parent, "entries": entries}


_DEFAULT_MANIFEST_PATH = Path("config/discovery-manifest.json")


def configure_sources(
    selected_paths: list[str],
    supported_extensions: list[str] | None = None,
    manifest_path: Path | None = None,
) -> dict:
    """Resolve selected folders/files into a discovery manifest.

    Each entry in ``selected_paths`` may be a directory (walked recursively for
    supported files, original behavior) or an individual file (added directly
    when its extension is supported — enables per-file selection from the file
    browser). Files are de-duplicated by resolved path, so selecting a file and
    its parent folder counts it once.

    Writes the manifest to ``manifest_path`` (default
    ``config/discovery-manifest.json``) and returns a summary with file counts
    and estimated processing time. ``manifest_path`` is optional so tests can
    redirect the write to a ``tmp_path`` and avoid mutating the tracked file.
    """
    if supported_extensions is None:
        config = load_discovery_config()
        supported_extensions = config["supported_extensions"]

    ext_set = set(supported_extensions)
    files: list[str] = []
    seen: set[str] = set()
    by_extension: dict[str, int] = {}

    def _add_file(f: Path) -> None:
        ext = f.suffix.lower()
        if ext not in ext_set:
            return
        resolved = str(f.resolve())
        if resolved in seen:
            return
        seen.add(resolved)
        files.append(resolved)
        by_extension[ext] = by_extension.get(ext, 0) + 1

    for raw_path in selected_paths:
        p = Path(raw_path)
        if p.is_file():
            if p.suffix.lower() in ext_set:
                _add_file(p)
            else:
                logger.warning("configure_sources_unsupported_file", path=raw_path)
            continue
        if not p.is_dir():
            logger.warning("configure_sources_path_not_found", path=raw_path)
            continue
        try:
            for f in p.rglob("*"):
                if f.is_file():
                    _add_file(f)
        except PermissionError:
            logger.warning("configure_sources_permission_denied", path=raw_path)

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_description": "Selected via onboarding UI",
        "files": files,
    }

    resolved_manifest_path = manifest_path or _DEFAULT_MANIFEST_PATH
    resolved_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_manifest_path.write_text(json.dumps(manifest, indent=2))

    # ~5 seconds per document on Apple Silicon
    estimated_minutes = round(len(files) * 5 / 60, 1)

    return {
        "manifest_path": str(resolved_manifest_path),
        "total_files": len(files),
        "by_extension": by_extension,
        "estimated_processing_minutes": estimated_minutes,
    }
