"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api/client";
import type { VelocityBand } from "@/lib/api/types";
import { changeDirectiveActorHeaders } from "@/lib/api/change-directives";
import { useSessionStore } from "@/lib/state/session-store";

export default function ChangeDirectivesListPage() {
  const sessionId = useSessionStore((s) => s.sessionId);
  const actor = sessionId ?? "00000000-0000-0000-0000-000000000000";
  const hdrs = useMemo(() => changeDirectiveActorHeaders(actor), [actor]);

  const [tier, setTier] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [band, setBand] = useState<VelocityBand | "">("");
  const [stalledOnly, setStalledOnly] = useState<boolean | null>(null);

  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await apiClient.listChangeDirectives(
          {
            limit: 200,
            tier: tier || undefined,
            status: status || undefined,
            velocity_band: band || undefined,
            is_stalled: stalledOnly ?? undefined,
          },
          hdrs,
        );
        if (!cancelled) setItems((res.items as Record<string, unknown>[]) ?? []);
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : "load failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hdrs, tier, status, band, stalledOnly]);

  return (
    <div className="p-4" data-testid="change-directives-list-page">
      <h1 className="mb-3 text-lg font-semibold">Change directives</h1>
      {err ? <p className="text-sm text-red-600">{err}</p> : null}
      <div
        className="mb-3 flex flex-wrap gap-2 text-xs"
        data-testid="cd-list-filters"
      >
        <label className="flex items-center gap-1">
          Tier
          <select
            className="rounded border px-1"
            value={tier}
            onChange={(e) => setTier(e.target.value)}
          >
            <option value="">any</option>
            <option value="Operational_Adjustment">Operational_Adjustment</option>
            <option value="Strategic_Initiative">Strategic_Initiative</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          Status
          <select
            className="rounded border px-1"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">any</option>
            <option value="draft">draft</option>
            <option value="active">active</option>
            <option value="realized">realized</option>
            <option value="abandoned">abandoned</option>
            <option value="superseded">superseded</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          Velocity band
          <select
            className="rounded border px-1"
            value={band}
            onChange={(e) => setBand(e.target.value as VelocityBand | "")}
          >
            <option value="">any</option>
            <option value="accelerating">accelerating</option>
            <option value="steady">steady</option>
            <option value="slowing">slowing</option>
            <option value="stalled">stalled</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          Stalled
          <select
            className="rounded border px-1"
            value={stalledOnly === null ? "" : stalledOnly ? "yes" : "no"}
            onChange={(e) => {
              const v = e.target.value;
              setStalledOnly(v === "" ? null : v === "yes");
            }}
          >
            <option value="">any</option>
            <option value="yes">yes</option>
            <option value="no">no</option>
          </select>
        </label>
      </div>
      <ul className="space-y-2">
        {items.map((row) => {
          const id = String(row.directive_id);
          const vb = row.velocity_band ? String(row.velocity_band) : "—";
          const st = row.is_stalled ? "stalled" : "not stalled";
          return (
            <li key={id}>
              <Link
                href={`/change-directives/${id}`}
                className="block rounded border bg-white p-2 text-sm hover:bg-slate-50"
              >
                <span className="font-medium">{String(row.title)}</span>
                <span className="ml-2 text-xs text-slate-500">
                  [{String(row.status)}] band: {vb} · {st}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
