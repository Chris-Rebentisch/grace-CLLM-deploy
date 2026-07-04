"use client";

/**
 * /permissions/ratify/[run_id] — ratification flow (Chunk 42, D331/D333).
 *
 * Sequence: hypothesis review → structured-form interview → matrix
 * preview → ratify. v1 surfaces the hypothesis run artifact, lets the
 * operator step through clusters with the move menu fallback (D323),
 * then opens the {@link PermissionMatrixRatifyDialog} to commit.
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { permissionsApi } from "@/lib/api/permissions";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";
import { RoleClusterCard } from "@/components/permissions/RoleClusterCard";
import { RoleClusterMemberCardSort } from "@/components/permissions/RoleClusterMemberCardSort";
import { PermissionMatrixRatifyDialog } from "@/components/permissions/PermissionMatrixRatifyDialog";
import type {
  HypothesisConfidenceBand,
  RoleClusterSummary,
} from "@/lib/api/types";

type HypothesisRun = {
  run_id: string;
  evidence_id: string;
  status: string;
  clusters?: Array<{
    cluster_id: string;
    display_name: string;
    member_grace_ids: string[];
    hypothesis_confidence_band: HypothesisConfidenceBand;
    sensitivity_tag?: string | null;
  }>;
};

export default function PermissionsRatifyPage() {
  const params = useParams<{ run_id: string }>();
  const run_id = params?.run_id ?? "";
  const [run, setRun] = useState<HypothesisRun | null>(null);
  const [clusters, setClusters] = useState<RoleClusterSummary[]>([]);
  const [showRatify, setShowRatify] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    permissionsApi
      .getHypothesisRun(run_id)
      .then((resp) => {
        if (cancelled) return;
        const r = resp as unknown as HypothesisRun;
        setRun(r);
        setClusters(r.clusters ?? []);
      })
      .catch((e) => {
        if (!cancelled)
          setErr(e instanceof Error ? e.message : "Failed to load run");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [run_id]);

  const matrixPayload = useMemo(
    () => ({
      hypothesis_run_id: run_id,
      role_clusters: clusters,
    }),
    [run_id, clusters],
  );

  const onMoveMember = (
    memberGraceId: string,
    fromClusterId: string,
    toClusterId: string,
  ) => {
    setClusters((prev) =>
      prev.map((c) => {
        if (c.cluster_id === fromClusterId) {
          return {
            ...c,
            member_grace_ids: c.member_grace_ids.filter(
              (m) => m !== memberGraceId,
            ),
          };
        }
        if (c.cluster_id === toClusterId) {
          return {
            ...c,
            member_grace_ids: [...c.member_grace_ids, memberGraceId],
          };
        }
        return c;
      }),
    );
  };

  return (
    <main
      data-testid="permissions-ratify-page"
      className="mx-auto flex max-w-5xl flex-col gap-4 p-4"
    >
      <h1 className="text-lg font-semibold text-slate-900">
        {PERMISSIONS_COPY.ratifyHeading}
      </h1>
      <p className="text-xs text-slate-600">
        Run id: <span className="font-mono">{run_id}</span>
      </p>

      {loading ? (
        <p className="text-xs text-slate-500">Loading hypothesis run…</p>
      ) : err ? (
        <p
          data-testid="permissions-ratify-error"
          className="text-xs text-rose-700"
        >
          {err}
        </p>
      ) : (
        <>
          <section data-testid="permissions-ratify-hypothesis-review">
            <h2 className="mb-2 text-sm font-semibold text-slate-900">
              Cluster review
            </h2>
            {clusters.length === 0 ? (
              <p className="text-xs italic text-slate-500">
                No clusters in this run.
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {clusters.map((c) => (
                  <RoleClusterCard key={c.cluster_id} cluster={c} />
                ))}
              </div>
            )}
          </section>

          <section data-testid="permissions-ratify-card-sort">
            <h2 className="mb-2 text-sm font-semibold text-slate-900">
              Member assignments
            </h2>
            <RoleClusterMemberCardSort
              clusters={clusters}
              onMoveMember={onMoveMember}
            />
          </section>

          <div className="flex justify-end">
            <button
              type="button"
              data-testid="permissions-ratify-open-dialog"
              onClick={() => setShowRatify(true)}
              disabled={clusters.length === 0}
              className="rounded border border-emerald-500 bg-emerald-50 px-3 py-1 text-xs text-emerald-900 disabled:opacity-50"
            >
              {PERMISSIONS_COPY.ratifyConfirm}
            </button>
          </div>

          <PermissionMatrixRatifyDialog
            open={showRatify}
            onClose={() => setShowRatify(false)}
            matrix={matrixPayload}
            versionLabel={run?.run_id ? `from-run-${run.run_id.slice(0, 8)}` : null}
          />
        </>
      )}
    </main>
  );
}
