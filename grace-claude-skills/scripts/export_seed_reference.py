#!/usr/bin/env python3
"""STEP 3 (seed grounding, Option C, no LLM) — Build a domain seed reference for Claude.

Grounds the Claude-as-LLM ontology proposal against the SAME proven domain ontologies
the native path uses (FIBO / LKIF / Schema.org / PROV-O) — but with Claude as the
reasoner instead of gpt-oss. Reuses grace's own seed registry + `format_for_llm`, so the
reference text is identical to what the native schema extractor feeds its LLM.

It selects every registry seed source whose `domains` include the requested domain, plus
the universal sources (Schema.org, PROV-O), loads their parsed cache, and renders one
markdown reference Claude reads during grace-ontology-proposal to ALIGN type names +
hierarchy and fill `seed_source` / `seed_type_name` / `seed_alignment` / `provenance`.

No heat: reads parsed seed JSON only. (`--provision` parses missing RDF via rdflib — CPU,
still no gpt-oss.)

Usage:
  python3 export_seed_reference.py --domain legal
  python3 export_seed_reference.py --domain legal --provision     # parse+cache missing seeds first
  python3 export_seed_reference.py --domain corporate_structure --out ./workspace/seed_ref.md
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

from _common import add_grace_to_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grace-root", default=None)
    ap.add_argument("--domain", required=True, help="Domain to ground (e.g. legal, corporate_structure)")
    ap.add_argument("--out", default=None, help="Output md (default: workspace/seed_reference_<domain>.md)")
    ap.add_argument("--provision", action="store_true",
                    help="Parse+cache any selected seed lacking a parsed cache (rdflib, CPU)")
    args = ap.parse_args()

    add_grace_to_path(args.grace_root)  # chdir to repo root; relative paths resolve from here
    from src.discovery.seed_models import SeedReference  # noqa: E402
    from src.discovery.seed_parser import format_for_llm  # noqa: E402
    from src.discovery.seed_registry import load_seed_registry, get_source_by_id  # noqa: E402

    reg = load_seed_registry()
    # Sources whose domains include the requested domain + the universal sources.
    selected = [s for s in reg.sources if args.domain in (getattr(s, "domains", []) or [])]
    universal = [get_source_by_id(uid) for uid in reg.universal_sources]
    selected = list({s.id: s for s in (selected + [u for u in universal if u])}.values())
    if not selected:
        raise SystemExit(f"[seed] no registry sources for domain '{args.domain}'.")

    def _cache_index():
        return {os.path.splitext(os.path.basename(p))[0]: p for p in glob.glob("seeds/parsed/*.json")}

    parsed = _cache_index()

    if args.provision:
        missing = [s for s in selected if s.id not in parsed]
        if missing:
            print(f"[seed] provisioning {len(missing)} uncached seed(s): {[s.id for s in missing]}")
            try:
                from src.discovery.seed_provisioner import parse_and_cache_seeds  # noqa: E402
                parse_and_cache_seeds(missing)
                parsed = _cache_index()
            except Exception as exc:  # best-effort; RDF may be absent
                print(f"[seed] provision skipped ({exc}); continuing with cached seeds.")

    def load_ref(path: str) -> SeedReference:
        # The parsed cache predates two now-required wrapper fields; patch defaults.
        d = json.load(open(path, encoding="utf-8"))
        d.setdefault("source_files", [])
        d.setdefault("registry_version", reg.version)
        return SeedReference.model_validate(d)

    sections: list[str] = []
    included: list[str] = []
    skipped: list[str] = []
    for s in selected:
        if s.id in parsed:
            txt = format_for_llm(load_ref(parsed[s.id]))
            sections.append(f"## Seed source: {s.id} ({s.source_ontology})\n{s.description}\n\n{txt}")
            included.append(s.id)
        else:
            skipped.append(s.id)

    if not included:
        raise SystemExit(f"[seed] none of the selected sources are cached for '{args.domain}'. "
                         f"Run with --provision (needs the seed RDF on disk). Selected: {[s.id for s in selected]}")

    out = Path(args.out or f"workspace/seed_reference_{args.domain}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Seed ontology reference — domain: {args.domain}\n"
        f"# Proven domain ontologies to ALIGN the proposal against (Option C grounding).\n"
        f"# Included sources: {included}\n"
        + (f"# Uncached (run --provision to add): {skipped}\n" if skipped else "")
        + "#\n# HOW TO USE: where a proposed type/relationship matches a seed class, adopt its\n"
        + "# name/hierarchy and set seed_source / seed_type_name (or seed_rel_name) /\n"
        + "# seed_alignment, and provenance=\"seed_aligned\". Keep document-driven types the\n"
        + "# seed lacks (provenance=\"claude_authored\"). The seed grounds; it does not constrain.\n\n"
    )
    out.write_text(header + "\n\n".join(sections), encoding="utf-8")
    print(f"[seed] domain={args.domain}: included {len(included)} source(s) {included}"
          + (f"; uncached {skipped}" if skipped else ""))
    print(f"[seed] wrote {out} — read it in grace-ontology-proposal to align + fill seed_source.")


if __name__ == "__main__":
    main()
