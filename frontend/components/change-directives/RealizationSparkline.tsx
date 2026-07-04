"use client";

import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { RealizationSnapshotPayload, VelocityBand } from "@/lib/api/types";

const BAND_RANK: Record<VelocityBand, number> = {
  stalled: 0,
  slowing: 1,
  steady: 2,
  accelerating: 3,
};

type Row = { idx: number; rank: number; at: string; band: VelocityBand | null };

export function RealizationSparkline({
  snapshots,
}: {
  snapshots: RealizationSnapshotPayload[];
}) {
  if (snapshots.length < 3) {
    return (
      <p data-testid="sparkline-insufficient" className="text-xs text-slate-500">
        Not enough history for a trend chart yet.
      </p>
    );
  }

  const data: Row[] = snapshots.map((s, idx) => ({
    idx,
    rank: s.velocity_band ? BAND_RANK[s.velocity_band] : 1,
    at: s.snapshot_at.slice(0, 10),
    band: s.velocity_band ?? null,
  }));

  return (
    <div data-testid="realization-sparkline" className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <XAxis dataKey="at" tick={{ fontSize: 10 }} />
          <YAxis hide domain={[0, 3]} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.[0]) return null;
              const p = payload[0].payload as Row;
              return (
                <div className="rounded border bg-white px-2 py-1 text-xs shadow">
                  <div>{p.at}</div>
                  <div>{p.band ?? "unknown band"}</div>
                </div>
              );
            }}
          />
          <Line type="monotone" dataKey="rank" stroke="#334155" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
