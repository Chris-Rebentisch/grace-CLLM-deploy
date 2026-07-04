"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { proposalsApi, type ProposalItem } from "@/lib/api/proposals";
import { PROPOSALS_COPY } from "@/lib/proposals/copy";

/**
 * D120/D217: raw_confidence is never rendered as a numeric value.
 * Map to a band label instead.
 */
function confidenceBand(raw: number): string {
  if (raw >= 0.7) return PROPOSALS_COPY.confidenceBandHigh;
  if (raw >= 0.4) return PROPOSALS_COPY.confidenceBandMedium;
  return PROPOSALS_COPY.confidenceBandLow;
}

function priorityLabel(priority: string): string {
  switch (priority) {
    case "high":
      return PROPOSALS_COPY.priorityHigh;
    case "medium":
      return PROPOSALS_COPY.priorityMedium;
    case "low":
      return PROPOSALS_COPY.priorityLow;
    default:
      return priority;
  }
}

function statusColor(status: string): string {
  switch (status) {
    case "pending":
      return "bg-yellow-100 text-yellow-800";
    case "approved":
      return "bg-green-100 text-green-800";
    case "rejected":
      return "bg-red-100 text-red-800";
    case "modified":
      return "bg-blue-100 text-blue-800";
    case "deferred":
      return "bg-amber-100 text-amber-900";
    case "superseded":
      return "bg-slate-200 text-slate-700";
    default:
      return "bg-slate-100 text-slate-800";
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case "pending":
      return PROPOSALS_COPY.statusPending;
    case "approved":
      return PROPOSALS_COPY.statusApproved;
    case "rejected":
      return PROPOSALS_COPY.statusRejected;
    case "modified":
      return PROPOSALS_COPY.statusModified;
    case "deferred":
      return PROPOSALS_COPY.statusDeferred;
    case "superseded":
      return PROPOSALS_COPY.statusSuperseded;
    default:
      return status;
  }
}

export default function ProposalsListPage() {
  const [items, setItems] = useState<ProposalItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [tierFilter, setTierFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const res = await proposalsApi.list({
          tier: tierFilter ? Number(tierFilter) : undefined,
          status: statusFilter || undefined,
          limit: 100,
        });
        if (!cancelled) {
          setItems(res.items);
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : "Load failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tierFilter, statusFilter]);

  return (
    <main
      data-testid="proposals-list-page"
      className="mx-auto flex max-w-4xl flex-col gap-4 p-4"
    >
      <header>
        <h1 className="text-lg font-semibold">{PROPOSALS_COPY.pageTitle}</h1>
      </header>

      <nav
        className="flex flex-wrap gap-2 text-xs"
        data-testid="proposals-filters"
      >
        <label className="flex items-center gap-1">
          {PROPOSALS_COPY.filterTier}
          <select
            className="rounded border px-1"
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
          >
            <option value="">{PROPOSALS_COPY.filterAny}</option>
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3">3</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          {PROPOSALS_COPY.filterStatus}
          <select
            className="rounded border px-1"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">{PROPOSALS_COPY.filterAny}</option>
            <option value="pending">{PROPOSALS_COPY.statusPending}</option>
            <option value="approved">{PROPOSALS_COPY.statusApproved}</option>
            <option value="rejected">{PROPOSALS_COPY.statusRejected}</option>
            <option value="modified">{PROPOSALS_COPY.statusModified}</option>
            <option value="deferred">{PROPOSALS_COPY.statusDeferred}</option>
            <option value="superseded">{PROPOSALS_COPY.statusSuperseded}</option>
          </select>
        </label>
      </nav>

      {err ? <p className="text-sm text-red-600">{err}</p> : null}

      {!loading && items.length === 0 ? (
        <p className="text-sm text-slate-500">{PROPOSALS_COPY.emptyState}</p>
      ) : null}

      <section className="flex flex-col gap-2" data-testid="proposals-list">
        {items.map((p) => (
          <Link
            key={p.id}
            href={`/proposals/${p.id}`}
            className="flex items-center justify-between rounded border border-slate-200 bg-white p-3 hover:border-slate-400"
            data-testid="proposal-card"
          >
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{p.kgcl_command}</span>
              <span className="text-xs text-slate-500">
                Tier {p.change_tier} &middot; {priorityLabel(p.priority)} &middot;{" "}
                {p.evidence.ontology_module}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">
                {confidenceBand(p.raw_confidence)}
              </span>
              <span
                className={`rounded px-1.5 py-0.5 text-xs font-medium ${statusColor(p.status)}`}
              >
                {statusLabel(p.status)}
              </span>
            </div>
          </Link>
        ))}
      </section>
    </main>
  );
}
