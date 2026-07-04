"""A/B benchmark harness for Docling pipeline modes (Chunk 62, CP4, D443).

CLI harness that processes every PDF in a corpus directory through both pipeline modes
(standard and vlm), collects per-document metrics, and emits a comparison artifact.

D246 mirror — CLI-only, no FastAPI integration.

Usage:
    cd ~/grace && PYTHONPATH=. python3 scripts/benchmark/run_docling_benchmark.py \
        --corpus tests/discovery/fixtures/benchmark-corpus/ \
        --output benchmark-results.json
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Ensure OMP workaround is set before Docling imports
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from docling.document_converter import DocumentConverter  # noqa: E402


def _build_vlm_converter() -> DocumentConverter:
    """Build a VLM-mode DocumentConverter for benchmarking."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmPipelineOptions
    from docling.datamodel import vlm_model_specs
    from docling.document_converter import PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    vlm_options = vlm_model_specs.GRANITEDOCLING_TRANSFORMERS
    pipeline_options = VlmPipelineOptions(vlm_options=vlm_options)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=pipeline_options,
            ),
        }
    )


def _process_single(converter: DocumentConverter, pdf_path: Path) -> dict:
    """Process a single PDF and return metrics."""
    start = time.monotonic()
    try:
        result = converter.convert(str(pdf_path))
        doc = result.document
        elapsed = time.monotonic() - start
        text = doc.export_to_markdown()
        docling_json = json.loads(doc.model_dump_json())

        # Extract table-related metrics
        tables = docling_json.get("tables", [])
        total_cells = sum(
            len(t.get("data", {}).get("table_cells", []))
            for t in tables
        )

        return {
            "file": pdf_path.name,
            "status": "success",
            "elapsed_seconds": round(elapsed, 3),
            "word_count": len(text.split()),
            "text_length": len(text),
            "table_count": len(tables),
            "total_table_cells": total_cells,
            "text_elements": len(docling_json.get("texts", [])),
            "error": None,
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "file": pdf_path.name,
            "status": "error",
            "elapsed_seconds": round(elapsed, 3),
            "word_count": 0,
            "text_length": 0,
            "table_count": 0,
            "total_table_cells": 0,
            "text_elements": 0,
            "error": str(e),
        }


def _compare_table_fidelity(standard_result: dict, vlm_result: dict) -> dict:
    """Compare table-structure fidelity between modes (relative comparison, not TEDS)."""
    return {
        "table_count_diff": vlm_result["table_count"] - standard_result["table_count"],
        "cell_count_diff": vlm_result["total_table_cells"] - standard_result["total_table_cells"],
        "text_element_diff": vlm_result["text_elements"] - standard_result["text_elements"],
        "word_count_diff": vlm_result["word_count"] - standard_result["word_count"],
    }


def run_benchmark(corpus_dir: Path) -> dict:
    """Run the A/B benchmark over all PDFs in corpus_dir."""
    pdf_files = sorted(corpus_dir.glob("*.pdf"))

    if not pdf_files:
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "corpus_dir": str(corpus_dir),
            "pdf_count": 0,
            "results": [],
            "summary": {"note": "Empty corpus — no PDFs found."},
        }

    # Build both converters
    standard_converter = DocumentConverter()
    vlm_converter = _build_vlm_converter()

    results = []
    for pdf_path in pdf_files:
        standard_result = _process_single(standard_converter, pdf_path)
        vlm_result = _process_single(vlm_converter, pdf_path)
        comparison = _compare_table_fidelity(standard_result, vlm_result)

        results.append({
            "file": pdf_path.name,
            "standard": standard_result,
            "vlm": vlm_result,
            "comparison": comparison,
        })

    # Aggregate summary
    std_successes = [r["standard"] for r in results if r["standard"]["status"] == "success"]
    vlm_successes = [r["vlm"] for r in results if r["vlm"]["status"] == "success"]

    summary = {
        "total_pdfs": len(pdf_files),
        "standard_successes": len(std_successes),
        "vlm_successes": len(vlm_successes),
        "standard_avg_elapsed": (
            round(sum(r["elapsed_seconds"] for r in std_successes) / len(std_successes), 3)
            if std_successes else None
        ),
        "vlm_avg_elapsed": (
            round(sum(r["elapsed_seconds"] for r in vlm_successes) / len(vlm_successes), 3)
            if vlm_successes else None
        ),
        "standard_total_tables": sum(r["table_count"] for r in std_successes),
        "vlm_total_tables": sum(r["table_count"] for r in vlm_successes),
        "standard_total_cells": sum(r["total_table_cells"] for r in std_successes),
        "vlm_total_cells": sum(r["total_table_cells"] for r in vlm_successes),
    }

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "corpus_dir": str(corpus_dir),
        "pdf_count": len(pdf_files),
        "results": results,
        "summary": summary,
    }


def generate_markdown_summary(benchmark: dict) -> str:
    """Generate a human-readable Markdown summary from benchmark results."""
    lines = [
        "# Docling Pipeline Benchmark Results",
        "",
        f"**Timestamp:** {benchmark['timestamp']}",
        f"**Corpus:** {benchmark['corpus_dir']}",
        f"**PDFs processed:** {benchmark['pdf_count']}",
        "",
    ]

    summary = benchmark.get("summary", {})
    if summary.get("note"):
        lines.append(f"**Note:** {summary['note']}")
        return "\n".join(lines)

    lines.extend([
        "## Aggregate Summary",
        "",
        "| Metric | Standard | VLM |",
        "|---|---|---|",
        f"| Successes | {summary.get('standard_successes', 0)} | {summary.get('vlm_successes', 0)} |",
        f"| Avg elapsed (s) | {summary.get('standard_avg_elapsed', 'N/A')} | {summary.get('vlm_avg_elapsed', 'N/A')} |",
        f"| Total tables | {summary.get('standard_total_tables', 0)} | {summary.get('vlm_total_tables', 0)} |",
        f"| Total cells | {summary.get('standard_total_cells', 0)} | {summary.get('vlm_total_cells', 0)} |",
        "",
        "## Per-Document Results",
        "",
    ])

    for result in benchmark.get("results", []):
        lines.append(f"### {result['file']}")
        lines.append("")
        std = result["standard"]
        vlm = result["vlm"]
        comp = result["comparison"]
        lines.extend([
            f"- Standard: {std['status']} ({std['elapsed_seconds']}s, {std['table_count']} tables, {std['word_count']} words)",
            f"- VLM: {vlm['status']} ({vlm['elapsed_seconds']}s, {vlm['table_count']} tables, {vlm['word_count']} words)",
            f"- Comparison: table_count_diff={comp['table_count_diff']}, cell_count_diff={comp['cell_count_diff']}",
            "",
        ])

    return "\n".join(lines)


def main() -> None:
    """CLI entry point for the A/B benchmark harness."""
    parser = argparse.ArgumentParser(
        description="A/B benchmark: standard vs VLM pipeline modes (Chunk 62, D443)"
    )
    parser.add_argument(
        "--corpus",
        type=str,
        required=True,
        help="Directory containing PDF files to benchmark",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmark-results.json",
        help="Output JSON file path (default: benchmark-results.json)",
    )
    parser.add_argument(
        "--markdown",
        type=str,
        default=None,
        help="Optional Markdown summary output file path",
    )
    args = parser.parse_args()

    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_dir():
        print(f"Error: corpus directory '{corpus_dir}' does not exist", file=sys.stderr)
        sys.exit(1)

    print(f"Running benchmark on corpus: {corpus_dir}")
    benchmark = run_benchmark(corpus_dir)

    # Write JSON output
    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(benchmark, f, indent=2)
    print(f"JSON results written to: {output_path}")

    # Write Markdown summary
    md_summary = generate_markdown_summary(benchmark)
    md_path = Path(args.markdown) if args.markdown else output_path.with_suffix(".md")
    with open(md_path, "w") as f:
        f.write(md_summary)
    print(f"Markdown summary written to: {md_path}")

    # Print summary to stdout
    print("\n" + md_summary)


if __name__ == "__main__":
    main()
