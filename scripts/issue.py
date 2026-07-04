#!/usr/bin/env python3
"""Local-first user-issue tracker for GrACE (`.issues/`), with optional GitHub push.

One markdown file per issue under `.issues/`, YAML frontmatter for machine
state, prose for humans/agents. Local is the source of truth; GitHub Issues
is an optional one-way mirror (push only — no pull/sync).

Verbs:
    python3 scripts/issue.py new "Title of the problem" [--severity high] [--area retrieval] [--reporter name]
    python3 scripts/issue.py list [--status open|in-progress|fixed|wontfix|all]
    python3 scripts/issue.py show ISS-0001
    python3 scripts/issue.py close ISS-0001 [--status fixed|wontfix] [--note "what fixed it"]
    python3 scripts/issue.py push ISS-0001 [--dry-run]   # gh issue create; writes number/URL back
    python3 scripts/issue.py index                        # regenerate BUGS.md (never hand-edit it)

Cross-platform (macOS/Windows): stdlib + PyYAML only; `push` additionally
needs the `gh` CLI authenticated against the origin remote.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ISSUES_DIR = REPO_ROOT / ".issues"
BUGS_MD = REPO_ROOT / "BUGS.md"

STATUSES = ("open", "in-progress", "fixed", "wontfix")
SEVERITIES = ("low", "medium", "high", "critical")

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


# ---------------------------------------------------------------------------
# Frontmatter I/O
# ---------------------------------------------------------------------------

def _split(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        raise SystemExit(f"{path.name}: missing YAML frontmatter")
    return yaml.safe_load(m.group(1)) or {}, text[m.end():]


def _join(meta: dict, body: str) -> str:
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n{body}"


def _issue_files() -> list[Path]:
    if not ISSUES_DIR.is_dir():
        return []
    return sorted(p for p in ISSUES_DIR.glob("ISS-*.md"))


def _find(issue_id: str) -> Path:
    issue_id = issue_id.upper()
    if not issue_id.startswith("ISS-"):
        issue_id = f"ISS-{int(issue_id):04d}"
    for p in _issue_files():
        if p.name.startswith(issue_id):
            return p
    raise SystemExit(f"No issue file matching {issue_id} under {ISSUES_DIR}")


def _next_id() -> str:
    highest = 0
    for p in _issue_files():
        m = re.match(r"ISS-(\d+)", p.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"ISS-{highest + 1:04d}"


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:48] or "untitled"


def _today() -> str:
    return _dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def cmd_new(args) -> None:
    ISSUES_DIR.mkdir(exist_ok=True)
    issue_id = _next_id()
    meta = {
        "id": issue_id,
        "title": args.title,
        "status": "open",
        "severity": args.severity,
        "area": args.area,
        "reported": _today(),
        "reporter": args.reporter,
        "github_issue": None,
        "github_url": None,
    }
    body = (
        "\n## What happened\n\n(describe the problem — what you did, what you"
        " expected, what you got)\n\n## Repro / evidence\n\n(steps, API"
        " responses, log lines, file:line references)\n\n## Fix notes\n\n"
        "(filled when resolved — what changed and why)\n"
    )
    path = ISSUES_DIR / f"{issue_id}-{_slug(args.title)}.md"
    path.write_text(_join(meta, body), encoding="utf-8")
    cmd_index(args)
    print(f"created {path.relative_to(REPO_ROOT)}")
    print("fill in 'What happened' + 'Repro / evidence', then it's filed.")


def cmd_list(args) -> None:
    rows = []
    for p in _issue_files():
        meta, _ = _split(p)
        if args.status != "all" and meta.get("status") != args.status:
            continue
        gh = f"#{meta['github_issue']}" if meta.get("github_issue") else "-"
        rows.append(
            (meta.get("id", "?"), meta.get("status", "?"),
             meta.get("severity", "?"), meta.get("area") or "-",
             gh, meta.get("title", "?"))
        )
    if not rows:
        print(f"no issues with status '{args.status}'")
        return
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    for r in rows:
        print("  ".join(str(r[i]).ljust(widths[i]) for i in range(5)) + f"  {r[5]}")


def cmd_show(args) -> None:
    print(_find(args.id).read_text(encoding="utf-8"))


def cmd_close(args) -> None:
    path = _find(args.id)
    meta, body = _split(path)
    meta["status"] = args.status
    meta["closed"] = _today()
    if args.note:
        body = body.rstrip() + f"\n\n**Resolution ({_today()}):** {args.note}\n"
    path.write_text(_join(meta, body), encoding="utf-8")
    if meta.get("github_issue"):
        rc = subprocess.run(
            ["gh", "issue", "close", str(meta["github_issue"]),
             "--comment", f"Closed locally as {args.status}."
             + (f" {args.note}" if args.note else "")],
            cwd=REPO_ROOT,
        ).returncode
        print(f"gh issue close #{meta['github_issue']}: "
              + ("ok" if rc == 0 else f"FAILED rc={rc} (local close recorded)"))
    cmd_index(args)
    print(f"{meta['id']} -> {args.status}")


def cmd_push(args) -> None:
    path = _find(args.id)
    meta, body = _split(path)
    if meta.get("github_issue"):
        raise SystemExit(
            f"{meta['id']} already pushed as #{meta['github_issue']} ({meta.get('github_url')})"
        )
    gh_body = (
        body.strip()
        + f"\n\n---\n*Filed from local tracker `{path.relative_to(REPO_ROOT)}`"
        + f" (reported {meta.get('reported')}, severity {meta.get('severity')}).*"
    )
    cmd = [
        "gh", "issue", "create",
        "--title", f"[{meta['id']}] {meta['title']}",
        "--body", gh_body,
        "--label", "user-reported",
    ]
    if args.dry_run:
        print("DRY RUN — would execute:")
        print("  " + " ".join(cmd[:5]) + " --body <…> --label user-reported")
        return
    # `--label` fails if the label doesn't exist in the repo; create-on-miss.
    subprocess.run(
        ["gh", "label", "create", "user-reported",
         "--color", "d73a4a", "--description", "Filed from the local .issues/ tracker"],
        cwd=REPO_ROOT, capture_output=True,
    )
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"gh issue create failed:\n{result.stderr.strip()}")
    url = result.stdout.strip().splitlines()[-1]
    number = int(url.rstrip("/").rsplit("/", 1)[-1])
    meta["github_issue"] = number
    meta["github_url"] = url
    path.write_text(_join(meta, body), encoding="utf-8")
    cmd_index(args)
    print(f"pushed {meta['id']} -> {url}")


def cmd_index(_args) -> None:
    """Regenerate BUGS.md — a generated view, never hand-edited."""
    lines = [
        "# BUGS — user-reported issue index",
        "",
        "**Generated by `python3 scripts/issue.py index` — do not edit by hand.**",
        "Source of truth: one file per issue under [.issues/](.issues/)"
        " (see [.issues/README.md](.issues/README.md) for the workflow).",
        "",
        "| id | status | severity | area | github | reported | title |",
        "|----|--------|----------|------|--------|----------|-------|",
    ]
    files = _issue_files()
    open_count = 0
    for p in files:
        meta, _ = _split(p)
        if meta.get("status") in ("open", "in-progress"):
            open_count += 1
        gh = (
            f"[#{meta['github_issue']}]({meta.get('github_url')})"
            if meta.get("github_issue") else "—"
        )
        lines.append(
            f"| [{meta.get('id')}]({p.relative_to(REPO_ROOT).as_posix()}) "
            f"| {meta.get('status')} | {meta.get('severity')} "
            f"| {meta.get('area') or '—'} | {gh} "
            f"| {meta.get('reported')} | {meta.get('title')} |"
        )
    if not files:
        lines.append("| — | — | — | — | — | — | (no issues filed yet) |")
    lines += ["", f"*{len(files)} total, {open_count} open/in-progress.*", ""]
    BUGS_MD.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="issue.py", description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("new", help="file a new issue")
    p.add_argument("title")
    p.add_argument("--severity", choices=SEVERITIES, default="medium")
    p.add_argument("--area", default=None,
                   help="module/surface (retrieval, ingestion, ui, ...)")
    p.add_argument("--reporter", default="glennys")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("list", help="table of issues")
    p.add_argument("--status", default="open",
                   choices=list(STATUSES) + ["all"])
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="print one issue")
    p.add_argument("id")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("close", help="resolve an issue (closes GH mirror too)")
    p.add_argument("id")
    p.add_argument("--status", choices=["fixed", "wontfix"], default="fixed")
    p.add_argument("--note", default=None)
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("push", help="mirror one issue to GitHub Issues via gh")
    p.add_argument("id")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_push)

    p = sub.add_parser("index", help="regenerate BUGS.md")
    p.set_defaults(fn=cmd_index)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
