#!/usr/bin/env python3
"""STEP 2 (persist, no LLM) — Import Claude-authored CQs into GrACE's live pipeline.

Claude (the LLM) writes a cqs.json following templates/cqs.example.json. This
script validates each row against grace's own `CompetencyQuestion` Pydantic model
and persists via `bulk_create_cqs`, so the downstream CQ merge + ontology
proposal + review pipeline pick them up exactly as if generated natively — but
with ZERO gpt-oss inference.

Each CQ is tagged source=HUMAN_AUTHORED (operator-curated; these are reviewed
before import) with metadata_extra.authoring_method="combined-a3" for audit.

Usage:
  python3 import_cqs.py --in ./workspace/cqs.json
  python3 import_cqs.py --in ./workspace/cqs.json --status DRAFT --dry-run

cqs.json shape: a JSON list of objects, each:
  {
    "canonical_text": "What legal entities own real estate?",   # REQUIRED
    "cq_type": "RELATIONSHIP",        # SCOPING|VALIDATING|FOUNDATIONAL|RELATIONSHIP|METAPROPERTY|UNCLASSIFIED
    "domain": "legal",                # optional; falls back to --domain
    "priority": "HIGH",               # HIGH|MEDIUM|LOW|UNSET (optional)
    "evidence_files": ["deed_2019.pdf"]   # optional; resolved to linked_document_ids
  }
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import add_grace_to_path, get_session

VALID_TYPES = {"SCOPING", "VALIDATING", "FOUNDATIONAL", "RELATIONSHIP", "METAPROPERTY", "UNCLASSIFIED"}
VALID_STATUS = {"DRAFT", "ACCEPTED", "EDITED", "REJECTED", "OUT_OF_SCOPE"}


def _filename_to_id_map(db) -> dict[str, str]:
    from src.discovery.database import ProcessedDocumentRow  # noqa: E402

    return {r.file_name: str(r.id) for r in db.query(ProcessedDocumentRow).all()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grace-root", default=None)
    ap.add_argument("--in", dest="infile", required=True, help="Path to Claude-authored cqs.json")
    ap.add_argument("--domain", default="other", help="Fallback domain when a CQ omits one")
    ap.add_argument("--status", default="ACCEPTED", choices=sorted(VALID_STATUS),
                    help="Persisted status (default ACCEPTED so the canonical pipeline treats them as review-ready)")
    ap.add_argument("--dry-run", action="store_true", help="Validate only; do not write")
    args = ap.parse_args()

    add_grace_to_path(args.grace_root)
    from src.discovery.cq_models import CompetencyQuestion  # noqa: E402
    from src.discovery.cq_database import bulk_create_cqs  # noqa: E402

    raw = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("[import-cqs] cqs.json must be a JSON list of CQ objects.")

    db = get_session(args.grace_root)
    fmap = _filename_to_id_map(db)

    models: list = []
    errors: list[str] = []
    for i, item in enumerate(raw):
        text = (item.get("canonical_text") or item.get("question") or "").strip()
        if not text:
            errors.append(f"[{i}] missing canonical_text")
            continue
        cq_type = (item.get("cq_type") or "UNCLASSIFIED").upper()
        if cq_type not in VALID_TYPES:
            errors.append(f"[{i}] bad cq_type {cq_type!r}")
            continue
        linked = [fmap[f] for f in item.get("evidence_files", []) if f in fmap]
        try:
            models.append(
                CompetencyQuestion(
                    canonical_text=text,
                    cq_type=cq_type,
                    domain=item.get("domain") or args.domain,
                    priority=(item.get("priority") or "UNSET").upper(),
                    source="HUMAN_AUTHORED",
                    status=args.status,
                    linked_document_ids=linked,
                    generation_confidence=float(item.get("confidence", 0.0)),
                    metadata_extra={"authoring_method": "combined-a3",
                                    "rationale": item.get("rationale", "")},
                )
            )
        except Exception as exc:  # pydantic validation
            errors.append(f"[{i}] {exc}")

    print(f"[import-cqs] parsed {len(models)} valid / {len(errors)} rejected of {len(raw)}")
    for e in errors[:20]:
        print(f"  REJECT {e}")
    if not models:
        raise SystemExit("[import-cqs] nothing to import.")
    if args.dry_run:
        print("[import-cqs] --dry-run: no rows written.")
        return

    saved = bulk_create_cqs(db, models)
    db.commit()
    print(f"[import-cqs] wrote {len(saved)} CQs (status={args.status}, source=HUMAN_AUTHORED).")
    print("[import-cqs] NEXT: grace-ontology-proposal (no merge needed on the Claude path).")


if __name__ == "__main__":
    main()
