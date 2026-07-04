"use client";
import { useState } from "react";
import type { ClaimListFilters, ClaimRecord } from "@/lib/api/types";
import { useClaims } from "@/lib/query/claims";
import { ClaimEmptyState } from "./ClaimEmptyState";
import { ClaimErrorState } from "./ClaimErrorState";

const STATUS_OPTIONS = ["", "quarantined", "auto_accepted", "rejected", "superseded"] as const;
const VERDICT_OPTIONS = ["", "supported", "refuted", "pending", "contradiction"] as const;

export function ClaimList({
  onSelect,
  selectedClaimId,
}: {
  onSelect: (claim: ClaimRecord) => void;
  selectedClaimId: string | null;
}) {
  const [filters, setFilters] = useState<ClaimListFilters>({
    status: "quarantined",
  });
  const [cursor, setCursor] = useState<string | null>(null);
  const { data, isLoading, isError, refetch } = useClaims(filters, cursor);

  if (isError) return <ClaimErrorState onRetry={() => void refetch()} />;
  if (isLoading) {
    return (
      <p data-testid="claim-list-loading" className="p-3 text-sm text-slate-500">
        Loading claims…
      </p>
    );
  }
  if (!data || data.items.length === 0) return <ClaimEmptyState />;

  return (
    <div className="flex h-full flex-col" data-testid="claim-list">
      <div className="space-y-1 border-b p-2 text-xs" data-testid="claim-list-filters">
        <label className="flex items-center gap-1">
          Status
          <select
            data-testid="filter-status"
            value={filters.status ?? ""}
            onChange={(e) => {
              setCursor(null);
              setFilters((f) => ({ ...f, status: e.target.value || undefined }));
            }}
            className="rounded border px-1"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s || "any"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          Verdict
          <select
            data-testid="filter-verdict"
            value={filters.verdict ?? ""}
            onChange={(e) => {
              setCursor(null);
              setFilters((f) => ({ ...f, verdict: e.target.value || undefined }));
            }}
            className="rounded border px-1"
          >
            {VERDICT_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s || "any"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          Module
          <input
            data-testid="filter-module"
            value={filters.ontology_module ?? ""}
            onChange={(e) => {
              setCursor(null);
              setFilters((f) => ({
                ...f,
                ontology_module: e.target.value || undefined,
              }));
            }}
            className="w-24 rounded border px-1"
          />
        </label>
        <label className="flex items-center gap-1">
          Source doc
          <input
            data-testid="filter-source"
            value={filters.source_document_id ?? ""}
            onChange={(e) => {
              setCursor(null);
              setFilters((f) => ({
                ...f,
                source_document_id: e.target.value || undefined,
              }));
            }}
            className="w-32 rounded border px-1"
          />
        </label>
      </div>
      <ul className="flex-1 overflow-y-auto" data-testid="claim-list-items">
        {data.items.map((c) => (
          <li key={c.claim_id}>
            <button
              type="button"
              data-testid={`claim-row-${c.claim_id}`}
              aria-current={selectedClaimId === c.claim_id ? "true" : undefined}
              onClick={() => onSelect(c)}
              className={`block w-full border-b px-3 py-2 text-left text-xs hover:bg-slate-50 ${
                selectedClaimId === c.claim_id ? "bg-slate-100" : ""
              }`}
            >
              <div className="font-medium text-slate-800">{c.subject_name}</div>
              <div className="text-slate-500">
                {c.entity_type ?? c.relationship_type ?? "—"} • {c.ontology_module ?? "?"}
              </div>
            </button>
          </li>
        ))}
      </ul>
      <div className="flex justify-between border-t p-2 text-xs">
        <button
          type="button"
          disabled={!cursor}
          onClick={() => setCursor(null)}
          data-testid="claim-list-reset"
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          First page
        </button>
        <button
          type="button"
          disabled={!data.next_cursor}
          onClick={() => setCursor(data.next_cursor)}
          data-testid="claim-list-next"
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Next page
        </button>
      </div>
    </div>
  );
}
