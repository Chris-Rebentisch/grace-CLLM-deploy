"use client";

/**
 * RoleClusterCard — D333.
 *
 * Per-cluster card. Renders the cluster's display name, member count,
 * and a `HypothesisConfidenceBand` badge. D120/D217: bands only — never
 * a numeric confidence score.
 */

import type {
  HypothesisConfidenceBand,
  RoleClusterSummary,
} from "@/lib/api/types";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";

const BAND_COPY: Record<HypothesisConfidenceBand, string> = {
  strong: PERMISSIONS_COPY.hypothesisConfidenceStrong,
  moderate: PERMISSIONS_COPY.hypothesisConfidenceModerate,
  weak: PERMISSIONS_COPY.hypothesisConfidenceWeak,
};

const BAND_CLASSES: Record<HypothesisConfidenceBand, string> = {
  strong: "border-emerald-500 bg-emerald-50 text-emerald-900",
  moderate: "border-amber-500 bg-amber-50 text-amber-900",
  weak: "border-rose-500 bg-rose-50 text-rose-900",
};

export type RoleClusterCardProps = {
  cluster: RoleClusterSummary;
  onSelect?: (clusterId: string) => void;
};

export function RoleClusterCard({ cluster, onSelect }: RoleClusterCardProps) {
  const memberCount = cluster.member_grace_ids.length;
  return (
    <div
      data-testid={`role-cluster-card-${cluster.cluster_id}`}
      className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-3 shadow-sm"
    >
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          data-testid={`role-cluster-card-select-${cluster.cluster_id}`}
          onClick={() => onSelect?.(cluster.cluster_id)}
          className="text-left text-sm font-semibold text-slate-900 hover:underline"
        >
          {cluster.display_name}
        </button>
        <span
          data-testid={`hypothesis-confidence-band-${cluster.cluster_id}`}
          aria-label={`Hypothesis confidence: ${BAND_COPY[cluster.hypothesis_confidence_band]}`}
          className={`rounded border px-2 py-0.5 text-[10px] font-medium uppercase ${BAND_CLASSES[cluster.hypothesis_confidence_band]}`}
        >
          {BAND_COPY[cluster.hypothesis_confidence_band]}
        </span>
      </div>
      <p className="text-xs text-slate-700">
        {memberCount} {memberCount === 1 ? "member" : "members"}
      </p>
      {cluster.sensitivity_tag ? (
        <p
          data-testid={`role-cluster-sensitivity-${cluster.cluster_id}`}
          className="text-[10px] text-slate-500"
        >
          Sensitivity: {cluster.sensitivity_tag}
        </p>
      ) : null}
    </div>
  );
}
