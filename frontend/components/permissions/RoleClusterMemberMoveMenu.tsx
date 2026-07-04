"use client";

/**
 * RoleClusterMemberMoveMenu — non-drag accessibility fallback (D323 pattern).
 *
 * Mirrors the keyboard-accessible move menu shipped with Decomposition's
 * `SegmentMoveMenu`: every action that the drag-between-clusters card
 * sort exposes via pointer must also be reachable via this menu.
 */

import { useState } from "react";
import type { RoleClusterSummary } from "@/lib/api/types";

export type RoleClusterMemberMoveMenuProps = {
  memberGraceId: string;
  fromClusterId: string;
  clusters: RoleClusterSummary[];
  onMove: (memberGraceId: string, toClusterId: string) => void;
};

export function RoleClusterMemberMoveMenu({
  memberGraceId,
  fromClusterId,
  clusters,
  onMove,
}: RoleClusterMemberMoveMenuProps) {
  const [open, setOpen] = useState(false);
  const targets = clusters.filter((c) => c.cluster_id !== fromClusterId);

  return (
    <div
      data-testid={`role-cluster-member-move-menu-${memberGraceId}`}
      className="relative inline-block"
    >
      <button
        type="button"
        data-testid={`role-cluster-member-move-trigger-${memberGraceId}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[10px] text-slate-700"
      >
        Move
      </button>
      {open ? (
        <ul
          role="menu"
          data-testid={`role-cluster-member-move-list-${memberGraceId}`}
          className="absolute right-0 z-10 mt-1 min-w-[160px] rounded-md border border-slate-300 bg-white p-1 shadow-md"
        >
          {targets.length === 0 ? (
            <li className="px-2 py-1 text-[11px] text-slate-500">
              No other clusters
            </li>
          ) : (
            targets.map((c) => (
              <li key={c.cluster_id}>
                <button
                  type="button"
                  role="menuitem"
                  data-testid={`role-cluster-member-move-target-${memberGraceId}-${c.cluster_id}`}
                  onClick={() => {
                    onMove(memberGraceId, c.cluster_id);
                    setOpen(false);
                  }}
                  className="block w-full rounded px-2 py-1 text-left text-[11px] text-slate-800 hover:bg-slate-100"
                >
                  {c.display_name}
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}
