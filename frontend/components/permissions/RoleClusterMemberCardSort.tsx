"use client";

/**
 * RoleClusterMemberCardSort — drag-between-clusters via D225 CardSortCanvas.
 *
 * v1 keeps the drag layer unimplemented and relies on the keyboard-
 * accessible {@link RoleClusterMemberMoveMenu} fallback (D323). This
 * component renders the visible per-cluster member columns and exposes
 * the move menu inline so operators reach all functionality without
 * pointer events.
 *
 * Chunk 43 wires the actual `@dnd-kit/react` drag handlers (the canvas
 * is already installed for `CardSortCanvas`); the v1 surface deliberately
 * stays declarative so the EC-12 copy registry + accessibility fallback
 * are exercised.
 */

import type { RoleClusterSummary } from "@/lib/api/types";
import { RoleClusterMemberMoveMenu } from "./RoleClusterMemberMoveMenu";

export type RoleClusterMemberCardSortProps = {
  clusters: RoleClusterSummary[];
  onMoveMember: (
    memberGraceId: string,
    fromClusterId: string,
    toClusterId: string,
  ) => void;
};

export function RoleClusterMemberCardSort({
  clusters,
  onMoveMember,
}: RoleClusterMemberCardSortProps) {
  return (
    <div
      data-testid="role-cluster-member-card-sort"
      className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3"
    >
      {clusters.map((cluster) => (
        <div
          key={cluster.cluster_id}
          data-testid={`role-cluster-member-column-${cluster.cluster_id}`}
          className="rounded-md border border-slate-200 bg-slate-50 p-2"
        >
          <h4 className="mb-2 text-xs font-semibold text-slate-900">
            {cluster.display_name}
          </h4>
          <ul className="flex flex-col gap-1">
            {cluster.member_grace_ids.length === 0 ? (
              <li className="text-[11px] italic text-slate-500">
                No members assigned
              </li>
            ) : (
              cluster.member_grace_ids.map((memberId) => (
                <li
                  key={memberId}
                  data-testid={`role-cluster-member-row-${cluster.cluster_id}-${memberId}`}
                  className="flex items-center justify-between rounded border border-slate-200 bg-white px-2 py-1"
                >
                  <span className="font-mono text-[11px] text-slate-800">
                    {memberId}
                  </span>
                  <RoleClusterMemberMoveMenu
                    memberGraceId={memberId}
                    fromClusterId={cluster.cluster_id}
                    clusters={clusters}
                    onMove={(mid, target) =>
                      onMoveMember(mid, cluster.cluster_id, target)
                    }
                  />
                </li>
              ))
            )}
          </ul>
        </div>
      ))}
    </div>
  );
}
