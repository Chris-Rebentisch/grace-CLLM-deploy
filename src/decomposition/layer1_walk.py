"""Layer 1 — filesystem walk over an operator-specified archive (D316).

Uses ``Path.walk(top_down=True, follow_symlinks=False)`` (Python
3.12+). Per-file inventory captures relative path, size, mtime,
suffix, and depth. Per-folder summary reports document count, size /
date range, suffix distribution, and a deterministic 10-by-size +
5-by-recency sample-title slice.

Hidden files are excluded by default; the operator can override via
``config.layer1.exclude_hidden=False``. Symlinks are never followed
(airgap-conservative). ``config.layer1.max_depth`` clamps the walk
when set.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.decomposition.config import DecompositionConfig
from src.decomposition.models import (
    Layer1FileEntry,
    Layer1FolderSummary,
    Layer1Summary,
)


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _depth(root: Path, p: Path) -> int:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return 0
    if str(rel) == ".":
        return 0
    return len(rel.parts)


def _mtime(p: Path) -> datetime:
    ts = p.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _build_folder_summary(
    folder_root: Path,
    archive_root: Path,
    files: list[Layer1FileEntry],
    title_lookup: dict[str, str],
) -> Layer1FolderSummary:
    """Aggregate stats over ``files`` belonging to ``folder_root``."""
    total_size = sum(f.size_bytes for f in files)
    suffix_dist: dict[str, int] = {}
    for f in files:
        suffix_dist[f.suffix] = suffix_dist.get(f.suffix, 0) + 1

    if files:
        oldest = min(f.mtime for f in files)
        newest = max(f.mtime for f in files)
    else:
        oldest = None
        newest = None

    # 10-by-size (largest first) + 5-by-recency (newest first), dedup
    # while preserving by-size-then-by-recency order. Deterministic on
    # (size, mtime, relative_path) tie-break.
    by_size = sorted(
        files,
        key=lambda f: (-f.size_bytes, -f.mtime.timestamp(), f.relative_path),
    )[:10]
    by_recency = sorted(
        files,
        key=lambda f: (-f.mtime.timestamp(), -f.size_bytes, f.relative_path),
    )[:5]

    titles: list[str] = []
    seen: set[str] = set()
    for f in [*by_size, *by_recency]:
        if f.relative_path in seen:
            continue
        seen.add(f.relative_path)
        titles.append(title_lookup.get(f.relative_path, Path(f.relative_path).stem))

    try:
        folder_path = str(folder_root.relative_to(archive_root))
    except ValueError:
        folder_path = str(folder_root)
    if folder_path == ".":
        folder_path = ""

    return Layer1FolderSummary(
        path=folder_path,
        doc_count=len(files),
        total_size_bytes=total_size,
        oldest_mtime=oldest,
        newest_mtime=newest,
        suffix_distribution=suffix_dist,
        sample_titles=titles,
    )


def walk_archive(
    archive_root: Path,
    config: DecompositionConfig,
) -> Layer1Summary:
    """Walk ``archive_root`` and produce a ``Layer1Summary`` (D316)."""
    archive_root = Path(archive_root)
    if not archive_root.exists() or not archive_root.is_dir():
        raise FileNotFoundError(
            f"archive_root does not exist or is not a directory: {archive_root}"
        )

    exclude_hidden = config.layer1.exclude_hidden
    max_depth = config.layer1.max_depth

    files: list[Layer1FileEntry] = []
    files_by_folder: dict[str, list[Layer1FileEntry]] = {}
    title_lookup: dict[str, str] = {}

    for current_dir, subdirs, filenames in archive_root.walk(
        top_down=True, follow_symlinks=False
    ):
        depth = _depth(archive_root, current_dir)

        if exclude_hidden:
            subdirs[:] = [s for s in subdirs if not _is_hidden(s)]
        if max_depth is not None and depth >= max_depth:
            subdirs[:] = []

        for name in filenames:
            if exclude_hidden and _is_hidden(name):
                continue
            full = current_dir / name
            # Defensive symlink filter — Path.walk already honours
            # follow_symlinks=False but a stat-time symlink check
            # protects against in-walk file replacement.
            if full.is_symlink():
                continue
            try:
                stat = full.stat()
            except OSError:
                continue

            rel = str(full.relative_to(archive_root))
            entry = Layer1FileEntry(
                relative_path=rel,
                size_bytes=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                suffix=full.suffix.lower(),
                depth=_depth(archive_root, full),
            )
            files.append(entry)
            files_by_folder.setdefault(str(current_dir), []).append(entry)
            title_lookup[rel] = full.stem

    folders: list[Layer1FolderSummary] = []
    for folder_path, folder_files in sorted(files_by_folder.items()):
        folders.append(
            _build_folder_summary(
                Path(folder_path),
                archive_root,
                folder_files,
                title_lookup,
            )
        )

    return Layer1Summary(
        archive_root=str(archive_root),
        total_files=len(files),
        files=files,
        folders=folders,
    )
