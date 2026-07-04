"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { apiClient } from "@/lib/api/client";
import type { RealizationSnapshotPayload } from "@/lib/api/types";
import { changeDirectiveActorHeaders } from "@/lib/api/change-directives";
import { useSessionStore } from "@/lib/state/session-store";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { EventFactory } from "@/lib/telemetry/events";
import { ConfirmRealizationCTA } from "@/components/change-directives/ConfirmRealizationCTA";
import { EvidenceCriterionResultsTable } from "@/components/change-directives/EvidenceCriterionResultsTable";
import { RealizationSparkline } from "@/components/change-directives/RealizationSparkline";
import { StalledBanner } from "@/components/change-directives/StalledBanner";

export default function ChangeDirectiveDetailPage() {
  const params = useParams<{ directive_id: string }>();
  const directiveId = params.directive_id;
  const sessionId = useSessionStore((s) => s.sessionId);
  const actor = sessionId ?? "00000000-0000-0000-0000-000000000000";
  const hdrs = useMemo(() => changeDirectiveActorHeaders(actor), [actor]);

  const [directive, setDirective] = useState<Record<string, unknown> | null>(null);
  const [history, setHistory] = useState<RealizationSnapshotPayload[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const d = await apiClient.getChangeDirective(directiveId, hdrs);
        const snaps = (await apiClient.listChangeDirectiveSnapshots(
          directiveId,
          30,
          hdrs,
        )) as RealizationSnapshotPayload[];
        if (!cancelled) {
          setDirective(d);
          setHistory(snaps);
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : "load failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [directiveId, hdrs]);

  useEffect(() => {
    if (!directive || !sessionId) return;
    const tier = directive.tier as "Operational_Adjustment" | "Strategic_Initiative";
    void postElicitationEvent(
      EventFactory.changeDirectiveDetailViewed(sessionId, {
        directive_id: directiveId,
        tier,
        viewer_user_id: sessionId,
      }),
    );
  }, [directive, directiveId, sessionId]);

  if (err) {
    return <p className="p-4 text-sm text-red-600">{err}</p>;
  }
  if (!directive) {
    return <p className="p-4 text-sm text-slate-500">Loading…</p>;
  }

  const latestRaw = directive.latest_snapshot as RealizationSnapshotPayload | null | undefined;
  const latest = latestRaw ?? null;
  const tier = directive.tier as "Operational_Adjustment" | "Strategic_Initiative";
  const status = String(directive.status);

  return (
    <div className="space-y-4 p-4" data-testid="change-directive-detail-page">
      <div>
        <h1 className="text-lg font-semibold">{String(directive.title)}</h1>
        <p className="text-xs text-slate-500">
          {tier} · {status}
        </p>
      </div>

      {latest?.is_stalled ? <StalledBanner /> : null}

      <section>
        <h2 className="mb-2 text-sm font-medium">Realization history</h2>
        <RealizationSparkline snapshots={history} />
      </section>

      {latest ? (
        <section>
          <h2 className="mb-2 text-sm font-medium">Latest criterion results</h2>
          <EvidenceCriterionResultsTable rows={latest.criteria_results} />
        </section>
      ) : null}

      <ConfirmRealizationCTA
        directiveId={directiveId}
        tier={tier}
        status={status}
        effectiveDate={(directive.effective_date as string | null) ?? null}
        latest={latest}
        actorUserId={actor}
      />
    </div>
  );
}
