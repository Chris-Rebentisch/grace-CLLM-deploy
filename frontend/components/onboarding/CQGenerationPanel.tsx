"use client";
import { useRef, useState } from "react";
import { apiClient } from "@/lib/api/client";

type Phase = "idle" | "running" | "stopping" | "merging" | "done" | "error";

/**
 * Generates competency questions FROM the processed documents (CQ-first
 * discovery). The LLM reads document text and proposes the questions the
 * domain needs — so a brand-new client starts with zero questions and GrACE
 * discovers them, rather than reusing a prior corpus's CQs.
 *   POST /api/discovery/generate-cqs -> poll generation-status (completed_at)
 *
 * On a successful (non-cancelled) generation it auto-runs the three-tier CQ
 * merge so the operator reviews the collapsed canonical set instead of the
 * raw, redundant output:
 *   POST /api/discovery/merge-cqs -> poll merge-status (status="completed")
 * A stopped/partial generation skips the auto-merge.
 */
export function CQGenerationPanel({
  docCount,
  cqCount,
  onGenerated,
  onMerged,
}: {
  docCount: number;
  cqCount: number;
  onGenerated: (n: number) => void;
  // Fired when the auto-merge completes, so the parent can refresh the
  // canonical review-set count it shows in the header.
  onMerged?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>(cqCount > 0 ? "done" : "idle");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [canonicalCount, setCanonicalCount] = useState<number | null>(null);
  const [rawCount, setRawCount] = useState<number | null>(null);
  const [mergeWarning, setMergeWarning] = useState("");
  const stoppingRef = useRef(false);

  // Tier-1/2/3 merge: cluster near-duplicates -> collapsed canonical review set.
  // Runs after generation; merge has no cancel endpoint, so no Stop here.
  async function runMerge(rawTotal: number) {
    setPhase("merging");
    setMergeWarning("");
    setMsg("Clustering and de-duplicating into a canonical review set…");
    const begin = Date.now();
    try {
      const start = await apiClient.mergeCqs({});
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const s = await apiClient.getCqMergeStatus(start.run_id);
        if (s.error) throw new Error(s.error);
        if (s.status === "failed") {
          throw new Error(s.error_message || "merge failed");
        }
        if (s.status === "completed" || s.completed_at) {
          setCanonicalCount(s.canonical_count ?? null);
          setRawCount(s.total_cqs_input ?? rawTotal);
          setMsg("");
          setPhase("done");
          onMerged?.();
          return;
        }
        // Tier-3 makes several local-LLM calls; this can run many minutes.
        if (Date.now() - begin > 1_800_000) throw new Error("CQ merge timed out");
        await new Promise((r) => setTimeout(r, 3000));
      }
    } catch (e) {
      // Generation already succeeded and the raw CQs are persisted — a merge
      // failure is non-fatal. Surface a warning but leave the questions usable.
      setMergeWarning(
        e instanceof Error
          ? `Questions were generated, but auto-merge failed (${e.message}). You can run merge from the review screen.`
          : "Questions were generated, but auto-merge failed. You can run merge from the review screen.",
      );
      setMsg("");
      setPhase("done");
    }
  }

  async function generate() {
    setError("");
    setMergeWarning("");
    setCanonicalCount(null);
    setRawCount(null);
    setPhase("running");
    setMsg("Reading documents…");
    stoppingRef.current = false;
    try {
      const start = await apiClient.generateCqs({});
      setRunId(start.run_id);
      const begin = Date.now();
      // GenerationRun has no status field — done when completed_at is set.
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const s = await apiClient.getGenerationStatus(start.run_id);
        if (s.error) throw new Error(s.error);
        if (s.completed_at) {
          const total = s.total_cqs_generated ?? 0;
          onGenerated(total);
          setRunId(null);
          if (s.cancelled) {
            // Operator stopped deliberately — keep the partial set, skip merge.
            setMsg(`Stopped. Kept ${total} questions generated so far.`);
            setPhase("done");
            return;
          }
          // Auto-run the merge so the canonical set is what gets reviewed.
          await runMerge(total);
          return;
        }
        setMsg(
          stoppingRef.current
            ? "Stopping after the current pass…"
            : "Generating competency questions from your documents…",
        );
        if (Date.now() - begin > 900_000) throw new Error("CQ generation timed out");
        await new Promise((r) => setTimeout(r, 2500));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "CQ generation failed");
      setPhase("error");
      setRunId(null);
    }
  }

  async function stop() {
    if (!runId) return;
    stoppingRef.current = true;
    setPhase("stopping");
    setMsg("Stopping after the current pass…");
    try {
      await apiClient.cancelGeneration(runId);
    } catch {
      // Poll loop still owns terminal state; ignore a failed cancel signal.
    }
  }

  const busy = phase === "running" || phase === "stopping" || phase === "merging";

  return (
    <div
      data-testid="cq-generation-panel"
      className="space-y-2 rounded border bg-white p-3 text-xs"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Generate competency questions</h2>
        <div className="flex items-center gap-2">
          {(phase === "running" || phase === "stopping") && (
            <button
              type="button"
              data-testid="stop-cqs"
              disabled={phase === "stopping" || !runId}
              onClick={() => void stop()}
              className="rounded bg-rose-600 px-3 py-1 text-white disabled:opacity-50"
            >
              {phase === "stopping" ? "Stopping…" : "Stop"}
            </button>
          )}
          <button
            type="button"
            data-testid="generate-cqs"
            disabled={busy || docCount === 0}
            onClick={() => void generate()}
            className="rounded bg-sky-700 px-3 py-1 text-white disabled:opacity-50"
          >
            {phase === "merging"
              ? "Merging…"
              : phase === "running" || phase === "stopping"
                ? "Generating…"
                : cqCount > 0
                  ? `Regenerate (${cqCount} exist)`
                  : `Generate from ${docCount} docs`}
          </button>
        </div>
      </div>

      <p className="text-slate-500">
        GrACE reads your documents and proposes the questions your domain needs
        (CQ-first discovery, gpt-oss:120b), then automatically clusters
        near-duplicates into a canonical set. These drive the ontology proposal.
      </p>

      {busy && (
        <div data-testid="cq-progress" className="space-y-1">
          <div className="h-2 w-full overflow-hidden rounded bg-slate-100">
            <div
              className={`h-full w-2/3 animate-pulse ${
                phase === "stopping"
                  ? "bg-rose-400"
                  : phase === "merging"
                    ? "bg-violet-500"
                    : "bg-sky-500"
              }`}
            />
          </div>
          <p className="text-slate-600">{msg}</p>
        </div>
      )}

      {error && (
        <p data-testid="cq-error" className="text-rose-600">
          {error}
        </p>
      )}

      {phase === "done" && mergeWarning && (
        <p data-testid="cq-merge-warning" className="rounded bg-amber-50 p-2 text-amber-800">
          {mergeWarning}
        </p>
      )}

      {phase === "done" && msg && (
        <p data-testid="cq-stopped" className="rounded bg-amber-50 p-2 text-amber-800">
          {msg}
        </p>
      )}

      {phase === "done" && canonicalCount !== null && (
        <p data-testid="cq-canonical-ready" className="rounded bg-sky-50 p-2 text-slate-700">
          <strong>{canonicalCount}</strong> canonical competency questions ready
          {rawCount !== null && rawCount > canonicalCount ? (
            <> (collapsed from {rawCount} raw)</>
          ) : null}
          . Run &ldquo;Propose ontology&rdquo; below to turn these into a
          reviewable ontology.
        </p>
      )}

      {phase === "done" && canonicalCount === null && !msg && cqCount > 0 && (
        <p data-testid="cq-ready" className="rounded bg-sky-50 p-2 text-slate-700">
          <strong>{cqCount}</strong> competency questions ready (status: DRAFT).
          Run &ldquo;Propose ontology&rdquo; below to turn these into a
          reviewable ontology.
        </p>
      )}
    </div>
  );
}
