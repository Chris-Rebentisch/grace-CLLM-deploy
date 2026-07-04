"use client";

/**
 * /ingestion — Ingestion dashboard (Chunk 60, CP3).
 *
 * Shows run list, source health badges, triage funnel stacked bar.
 * Polls at ~3s while any run is running/pending; stops when idle.
 * Emits ingestion_dashboard_viewed telemetry on mount.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  fetchIngestionRuns,
  fetchIngestionSources,
  type IngestionRunItem,
  type IngestionSourceItem,
} from "@/lib/api/ingestion";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { buildEnvelope } from "@/lib/telemetry/events";
import { useSessionStore } from "@/lib/state/session-store";
import { INGESTION_COPY } from "@/lib/ingestion/copy";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

const POLL_INTERVAL_MS = 3_000;

function sourceStatusBadge(status: string): string {
  switch (status) {
    case "ready":
      return "border-emerald-300 bg-emerald-50 text-emerald-900";
    case "error":
      return "border-rose-300 bg-rose-50 text-rose-900";
    case "disabled":
      return "border-slate-300 bg-slate-50 text-slate-500";
    default:
      return "border-amber-300 bg-amber-50 text-amber-900";
  }
}

function runStatusBadge(status: string): string {
  switch (status) {
    case "running":
      return "border-blue-300 bg-blue-50 text-blue-900";
    case "completed":
      return "border-emerald-300 bg-emerald-50 text-emerald-900";
    case "failed":
      return "border-rose-300 bg-rose-50 text-rose-900";
    case "paused":
      return "border-amber-300 bg-amber-50 text-amber-900";
    default:
      return "border-slate-300 bg-slate-50 text-slate-500";
  }
}

type TriageFunnelData = {
  name: string;
  count: number;
  band: string;
};

function buildFunnelData(
  counts: Record<string, number> | null,
): TriageFunnelData[] {
  if (!counts) return [];
  const total = counts.total_processed || 1;
  const entries = [
    { key: "tier1_filtered", label: "Tier 1" },
    { key: "tier2_filtered", label: "Tier 2" },
    { key: "tier3_filtered", label: "Tier 3 filtered" },
    { key: "tier3_passed", label: "Tier 3 passed" },
    { key: "tier4_filtered", label: "Tier 4 filtered" },
    { key: "tier4_passed", label: "Tier 4 passed" },
  ];
  return entries
    .filter((e) => (counts[e.key] ?? 0) > 0)
    .map((e) => {
      const count = counts[e.key] ?? 0;
      const ratio = count / total;
      const band =
        ratio >= 0.95
          ? "High noise removal"
          : ratio >= 0.5
            ? "Moderate"
            : "Low";
      return { name: e.label, count, band };
    });
}

const FUNNEL_COLORS = [
  "#94a3b8",
  "#64748b",
  "#475569",
  "#334155",
  "#1e293b",
  "#0f172a",
];

export default function IngestionDashboardPage() {
  const [runs, setRuns] = useState<IngestionRunItem[]>([]);
  const [sources, setSources] = useState<IngestionSourceItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const cancelledRef = useRef(false);
  const sessionId = useSessionStore((s) => s.sessionId);
  const telemetrySent = useRef(false);

  const load = useCallback(async () => {
    try {
      const [runsRes, sourcesRes] = await Promise.all([
        fetchIngestionRuns(),
        fetchIngestionSources(),
      ]);
      if (!cancelledRef.current) {
        setRuns(runsRes.items);
        setSources(sourcesRes.items);
      }
    } catch (e) {
      if (!cancelledRef.current) {
        setErr(e instanceof Error ? e.message : "load failed");
      }
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void load();
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  // Telemetry on mount
  useEffect(() => {
    if (telemetrySent.current || !sessionId) return;
    telemetrySent.current = true;
    const activeCount = runs.filter(
      (r) => r.status === "running" || r.status === "pending",
    ).length;
    void postElicitationEvent(
      buildEnvelope({
        session_id: sessionId,
        phase_name: "none",
        event_type: "ingestion_dashboard_viewed",
        payload: { active_runs_count: activeCount },
      }),
    );
  }, [runs, sessionId]);

  // Poll while any run is running or pending.
  useEffect(() => {
    const anyActive = runs.some(
      (r) => r.status === "running" || r.status === "pending",
    );
    if (!anyActive) return;
    const id = setInterval(() => {
      void load();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [runs, load]);

  // Latest run for triage funnel
  const latestRun =
    runs.length > 0
      ? runs.find((r) => r.triage_tier_counts_json) ?? null
      : null;
  const funnelData = buildFunnelData(
    latestRun?.triage_tier_counts_json ?? null,
  );

  if (err) {
    return (
      <div className="p-4" data-testid="ingestion-dashboard-error">
        <p className="text-red-700">{err}</p>
      </div>
    );
  }

  if (runs.length === 0 && sources.length === 0) {
    return (
      <div className="p-4" data-testid="ingestion-dashboard-empty">
        <h1 className="mb-3 text-lg font-semibold">Ingestion</h1>
        <p className="text-slate-500">
          No ingestion runs yet.{" "}
          <Link href="/ingestion/setup" className="text-blue-600 underline">
            Set up a source
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="p-4" data-testid="ingestion-dashboard">
      <header className="mb-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Ingestion</h1>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
        >
          Refresh
        </button>
      </header>

      {/* Sources */}
      <section className="mb-6">
        <h2 className="mb-2 text-sm font-medium text-slate-600">Sources</h2>
        <div className="flex flex-wrap gap-2">
          {sources.map((s) => (
            <Link
              key={s.id}
              href={`/ingestion/${s.id}`}
              className={`rounded border px-3 py-1 text-xs font-medium ${sourceStatusBadge(s.status)}`}
              data-testid="source-badge"
            >
              {s.name}{" "}
              <span className="opacity-60">({s.status})</span>
              {s.status === "error" && s.source_type && ["exchange", "gmail", "imap"].includes(s.source_type) && (
                <span className="ml-1 text-rose-700">&mdash; re-consent needed</span>
              )}
            </Link>
          ))}
        </div>
      </section>

      {/* Triage funnel */}
      {funnelData.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 text-sm font-medium text-slate-600">
            Triage funnel
          </h2>
          <div className="h-40 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={funnelData} layout="vertical">
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" width={120} />
                <Tooltip
                  formatter={(_value: number, _name: string, props: { payload?: TriageFunnelData }) => [
                    props.payload?.band ?? "",
                    props.payload?.name ?? "",
                  ]}
                />
                <Bar dataKey="count" fill="#475569" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-xs text-slate-400" data-testid="funnel-band-label">
            Band labels only &mdash; no percentages (D120/D217)
          </p>
        </section>
      )}

      {/* Runs */}
      <section>
        <h2 className="mb-2 text-sm font-medium text-slate-600">Runs</h2>
        <ul className="space-y-1">
          {runs.map((r) => (
            <li
              key={r.id}
              className="flex items-center justify-between rounded border border-slate-200 px-3 py-2 text-sm"
              data-testid="run-row"
            >
              <span>
                {r.source_id}{" "}
                <span
                  className={`ml-2 rounded border px-2 py-0.5 text-xs font-medium ${runStatusBadge(r.status)}`}
                  data-testid="run-status-badge"
                >
                  {r.status}
                </span>
              </span>
              <span className="text-xs text-slate-400">
                {r.started_at ?? "not started"}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
