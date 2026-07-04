#!/usr/bin/env python3
"""STEP 1 (no LLM) — Export GrACE's processed documents into a corpus bundle
that Claude Desktop can read for CQ authoring and ontology proposal.

Reuses grace's own `build_balanced_document_text` so EVERY document in a domain
gets an equal share of the character budget (head/middle/tail sampled for long
docs) — the same balanced-coverage fix from the 2026-06-09 session. This is the
"docs, not top-10" grounding.

Output (default ./workspace/corpus/):
  <domain>.md        one markdown file per domain, ready to paste/attach to Claude
  manifest.json      domains, doc counts, char counts, file lists

Usage:
  python3 export_corpus.py                         # all domains, default budget
  python3 export_corpus.py --domain legal --domain insurance
  python3 export_corpus.py --max-chars 120000 --out ./workspace/corpus

NOTE: reads Postgres only. Does not touch Ollama / gpt-oss. No heat.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import add_grace_to_path, distinct_domains, get_session


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grace-root", default=None, help="Path to the grace repo (default ~/grace)")
    ap.add_argument("--domain", action="append", default=None,
                    help="Domain to export (repeatable). Default: every domain with COMPLETE docs.")
    ap.add_argument("--out", default="./workspace/corpus", help="Output directory")
    ap.add_argument("--max-chars", type=int, default=None,
                    help="Char budget per domain (default = config max_document_chars_per_batch)")
    args = ap.parse_args()

    add_grace_to_path(args.grace_root)
    from src.discovery.domain_batcher import build_balanced_document_text  # noqa: E402
    from src.discovery.database import ProcessedDocumentRow  # noqa: E402

    db = get_session(args.grace_root)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    domains = args.domain or distinct_domains(db)
    if not domains:
        raise SystemExit("[export] No COMPLETE processed documents found. Run document processing first.")

    manifest: dict = {"domains": [], "budget_chars": args.max_chars}
    for domain in domains:
        text = build_balanced_document_text(db, domain, max_chars=args.max_chars)
        files = [
            r.file_name
            for r in db.query(ProcessedDocumentRow)
            .filter(ProcessedDocumentRow.status == "COMPLETE", ProcessedDocumentRow.domain == domain)
            .all()
        ]
        path = out / f"{domain}.md"
        path.write_text(
            f"# GrACE corpus — domain: {domain}\n"
            f"# documents: {len(files)} | chars: {len(text)}\n"
            f"# files: {', '.join(files)}\n\n{text}\n",
            encoding="utf-8",
        )
        manifest["domains"].append(
            {"domain": domain, "documents": len(files), "chars": len(text),
             "files": files, "corpus_file": str(path)}
        )
        print(f"[export] {domain:<20} {len(files):>3} docs  {len(text):>8} chars -> {path}")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[export] manifest -> {out / 'manifest.json'}")
    print(f"[export] DONE. Attach the per-domain .md files to Claude for CQ authoring (skill: grace-cq-authoring).")


if __name__ == "__main__":
    main()
