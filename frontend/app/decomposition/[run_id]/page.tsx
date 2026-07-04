"use client";

/**
 * /decomposition/[run_id] — Decomposition run detail.
 *
 * Wires together D322 LowStabilityBadge, HypothesisDecisionBar
 * (Layer 5), SampleCqPanel (Layer 6), and SegmentationMapRatifyDialog
 * (Layer 7). EC-12 copy discipline applies to all visible strings.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { apiClient } from "@/lib/api/client";
import type {
  DecompositionRunDetail,
  Layer5DecisionPayload,
  SegmentationMap,
} from "@/lib/api/types";
import { HypothesisDecisionBar } from "@/components/decomposition/HypothesisDecisionBar";
import { LowStabilityBadge } from "@/components/decomposition/LowStabilityBadge";
import { SampleCqPanel, type SampleCq, type SampleCqDecision } from "@/components/decomposition/SampleCqPanel";
import { SegmentationMapRatifyDialog } from "@/components/decomposition/SegmentationMapRatifyDialog";

function readLowStabilityFlag(run: DecompositionRunDetail | null): boolean {
  if (!run) return false;
  const layer3 = (run.layer3_payload ?? {}) as Record<string, unknown>;
  return Boolean(layer3.low_stability_flag);
}

export default function DecompositionDetailPage() {
  const params = useParams<{ run_id: string }>();
  const runId = params.run_id;

  const [run, setRun] = useState<DecompositionRunDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sampleCqs, setSampleCqs] = useState<Record<string, SampleCq[]>>({});
  const [layer6Decisions, setLayer6Decisions] = useState<
    Record<string, SampleCqDecision[]>
  >({});
  const [ratifyOpen, setRatifyOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const detail = await apiClient.getDecompositionRun(runId);
      setRun(detail);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const lowStability = readLowStabilityFlag(run);

  const submitLayer5 = useCallback(
    async (
      kind: Layer5DecisionPayload["decision_kind"],
      rationale?: string,
    ) => {
      if (!run) return;
      setBusy(true);
      setErr(null);
      try {
        const payload: Layer5DecisionPayload = {
          decision_kind: kind,
          rationale: rationale ?? null,
        };
        await apiClient.submitDecompositionLayer5Decision(runId, payload);
        await load();
      } catch (e) {
        setErr(e instanceof Error ? e.message : "decision failed");
      } finally {
        setBusy(false);
      }
    },
    [load, run, runId],
  );

  const requestRerun = useCallback(
    async (direction: "finer" | "coarser") => {
      setBusy(true);
      try {
        await apiClient.triggerDecompositionRerun(runId, { direction });
        await load();
      } catch (e) {
        setErr(e instanceof Error ? e.message : "rerun failed");
      } finally {
        setBusy(false);
      }
    },
    [load, runId],
  );

  const generateSampleCqs = useCallback(
    async (segmentName: string) => {
      try {
        const res = await apiClient.generateDecompositionLayer6SampleCqs(
          runId,
          { segment_name: segmentName, n: 5 },
        );
        setSampleCqs((prev) => ({ ...prev, [segmentName]: res.cqs }));
      } catch (e) {
        setErr(e instanceof Error ? e.message : "sample-cq failed");
      }
    },
    [runId],
  );

  const segmentNames: string[] = useMemo(() => {
    if (!run?.layer4_payload) return [];
    const layer4 = run.layer4_payload as Record<string, unknown>;
    const hypotheses = (layer4.hypotheses ?? []) as Array<Record<string, unknown>>;
    const segs: string[] = [];
    for (const h of hypotheses) {
      const proposed = (h.proposed_segments ?? []) as Array<
        Record<string, unknown>
      >;
      for (const s of proposed) {
        const name = typeof s.name === "string" ? s.name : null;
        if (name && !segs.includes(name)) segs.push(name);
      }
    }
    return segs;
  }, [run]);

  const submitLayer6 = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const segments = Object.entries(layer6Decisions).map(([name, decisions]) => ({
        segment_name: name,
        cq_sample: decisions.map((d) => d.question),
        approved: decisions.every((d) => d.approved),
      }));
      await apiClient.submitDecompositionLayer6Validation(runId, { segments });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "validation submit failed");
    } finally {
      setBusy(false);
    }
  }, [layer6Decisions, load, runId]);

  const proposedMap: SegmentationMap | null = useMemo(() => {
    if (!run) return null;
    return {
      map_id: "00000000-0000-0000-0000-000000000000",
      decomposition_run_id: run.run_id,
      schema_version: "1.0",
      payload_hash: "pending",
      previous_hash: null,
      payload: {
        segments: segmentNames.map((n) => ({ name: n })),
      },
      null_hypothesis_accepted: false,
      created_at: new Date().toISOString(),
    };
  }, [run, segmentNames]);

  return (
    <div className="p-4" data-testid="decomposition-detail-page">
      <header className="mb-3">
        <h1 className="text-lg font-semibold">Decomposition run</h1>
        <p className="font-mono text-xs text-slate-500">{runId}</p>
        {run ? (
          <p className="text-xs text-slate-700">
            <span data-testid="decomposition-detail-status">{run.status}</span>
            {run.archive_root ? ` · ${run.archive_root}` : null}
          </p>
        ) : null}
      </header>

      <LowStabilityBadge
        visible={lowStability}
        onRerunRecommended={() => void requestRerun("finer")}
      />

      {err ? (
        <p data-testid="decomposition-detail-error" className="mb-2 text-xs text-rose-700">
          {err}
        </p>
      ) : null}

      <section className="mb-4" data-testid="decomposition-layer5-section">
        <h2 className="mb-2 text-sm font-medium">Layer 5 — interview</h2>
        <HypothesisDecisionBar
          disabled={busy || run?.status !== "paused_pre_layer5"}
          onDecide={(kind, rationale) => void submitLayer5(kind, rationale)}
        />
      </section>

      <section className="mb-4" data-testid="decomposition-layer6-section">
        <h2 className="mb-2 text-sm font-medium">Layer 6 — sample-CQ validation</h2>
        {segmentNames.length === 0 ? (
          <p className="text-xs text-slate-500">
            Layer 4 hypotheses not yet available.
          </p>
        ) : (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              {segmentNames.map((name) => (
                <button
                  key={name}
                  type="button"
                  data-testid={`generate-sample-cqs-${name}`}
                  onClick={() => void generateSampleCqs(name)}
                  className="rounded border border-blue-300 bg-blue-50 px-2 py-1 text-xs text-blue-900"
                >
                  Generate sample CQs · {name}
                </button>
              ))}
            </div>
            {Object.entries(sampleCqs).map(([segName, cqs]) => (
              <SampleCqPanel
                key={segName}
                segmentName={segName}
                cqs={cqs}
                onChange={(name, decisions) =>
                  setLayer6Decisions((prev) => ({ ...prev, [name]: decisions }))
                }
              />
            ))}
            {Object.keys(layer6Decisions).length > 0 ? (
              <button
                type="button"
                data-testid="decomposition-layer6-submit"
                onClick={() => void submitLayer6()}
                disabled={busy}
                className="rounded border border-emerald-500 bg-emerald-50 px-3 py-1 text-xs text-emerald-900 disabled:opacity-50"
              >
                Submit Layer 6 validation
              </button>
            ) : null}
          </div>
        )}
      </section>

      <section className="mb-4" data-testid="decomposition-layer7-section">
        <h2 className="mb-2 text-sm font-medium">Layer 7 — Segmentation Map</h2>
        <button
          type="button"
          data-testid="open-ratify-dialog"
          onClick={() => setRatifyOpen(true)}
          disabled={!proposedMap}
          className="rounded border border-emerald-500 bg-emerald-50 px-3 py-1 text-xs text-emerald-900 disabled:opacity-50"
        >
          Ratify segmentation map
        </button>
      </section>

      {proposedMap ? (
        <SegmentationMapRatifyDialog
          open={ratifyOpen}
          onClose={() => setRatifyOpen(false)}
          runId={runId}
          segmentationMap={proposedMap}
          onRatified={() => {
            void load();
          }}
        />
      ) : null}
    </div>
  );
}
