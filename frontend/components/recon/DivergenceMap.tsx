"use client";

// Cross-Executive Divergence Map UI (Chunk 37, D284 / D288).
//
// All user-facing strings come from `frontend/lib/recon/report_copy.ts`
// (EC-11 forbidden-token discipline). No Cytoscape; pure Tailwind grid.
// Tabbed fallback at narrow widths is implemented with a local
// `useState` switch rather than a Tabs primitive (no shadcn Tabs in
// this repo).

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CoveringDirectivesBanner } from "@/components/recon/CoveringDirectivesBanner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { emitTelemetry } from "@/lib/telemetry/bus";
import {
  DIVERGENCE_MAP_BUCKET_LABELS,
  DIVERGENCE_MAP_DRAWER_TITLE,
  DIVERGENCE_MAP_EMPTY_STATE,
  DIVERGENCE_MAP_EVIDENCE_BADGE,
  DIVERGENCE_MAP_SUBTITLE,
  DIVERGENCE_MAP_TABS_FALLBACK_HINT,
  DIVERGENCE_MAP_TITLE,
} from "@/lib/recon/report_copy";
import type {
  DivergenceMapBucket,
  DivergenceMapEntry,
  DivergenceMapResponse,
} from "@/lib/api/recon-types";

export type DivergenceMapProps = {
  data: DivergenceMapResponse;
  reviewerHash?: string;
};

function findBucket(
  buckets: DivergenceMapBucket[],
  name: DivergenceMapBucket["bucket_name"],
): DivergenceMapBucket | undefined {
  return buckets.find((b) => b.bucket_name === name);
}

export function DivergenceMap({ data, reviewerHash }: DivergenceMapProps) {
  const [activeTab, setActiveTab] = useState<"a" | "consensus" | "b">(
    "consensus",
  );
  const [drawerEntry, setDrawerEntry] = useState<DivergenceMapEntry | null>(
    null,
  );

  useEffect(() => {
    emitTelemetry("divergence_map_viewed", {
      reviewer_hash: reviewerHash ?? "",
      divergence_map_id: data.map_id,
      viewed_at: new Date().toISOString(),
    });
  }, [data.map_id, reviewerHash]);

  const additiveA = findBucket(data.buckets, "additive_A");
  const additiveB = findBucket(data.buckets, "additive_B");
  const consensus = findBucket(data.buckets, "consensus");
  const contradictory = findBucket(data.buckets, "contradictory");

  const totalEntries =
    (additiveA?.entries.length ?? 0) +
    (additiveB?.entries.length ?? 0) +
    (contradictory?.entries.length ?? 0);

  return (
    <div data-testid="divergence-map" className="flex flex-col gap-4 p-4">
      <header>
        <h2 className="text-lg font-semibold">{DIVERGENCE_MAP_TITLE}</h2>
        <p className="text-sm text-slate-500">{DIVERGENCE_MAP_SUBTITLE}</p>
      </header>

      <CoveringDirectivesBanner directives={data.covering_directives ?? []} />

      {totalEntries === 0 ? (
        <p
          data-testid="divergence-map-empty"
          className="rounded border border-dashed border-slate-300 p-4 text-sm text-slate-500"
        >
          {DIVERGENCE_MAP_EMPTY_STATE}
        </p>
      ) : null}

      {/* Narrow-width tabbed fallback. Visible below md. */}
      <div className="md:hidden" data-testid="divergence-map-tabs">
        <p className="mb-2 text-xs text-slate-500">
          {DIVERGENCE_MAP_TABS_FALLBACK_HINT}
        </p>
        <div className="flex gap-2" role="tablist">
          <Button
            variant={activeTab === "a" ? "default" : "outline"}
            size="sm"
            role="tab"
            aria-selected={activeTab === "a"}
            onClick={() => setActiveTab("a")}
          >
            {data.reviewer_a}
          </Button>
          <Button
            variant={activeTab === "consensus" ? "default" : "outline"}
            size="sm"
            role="tab"
            aria-selected={activeTab === "consensus"}
            onClick={() => setActiveTab("consensus")}
          >
            {DIVERGENCE_MAP_BUCKET_LABELS.consensus}
          </Button>
          <Button
            variant={activeTab === "b" ? "default" : "outline"}
            size="sm"
            role="tab"
            aria-selected={activeTab === "b"}
            onClick={() => setActiveTab("b")}
          >
            {data.reviewer_b}
          </Button>
        </div>
        <div className="mt-3">
          {activeTab === "a" ? (
            <Column
              title={`${data.reviewer_a}: ${DIVERGENCE_MAP_BUCKET_LABELS.additive_A}`}
              bucket={additiveA}
              onSelect={setDrawerEntry}
              testId="divergence-map-tab-a"
            />
          ) : null}
          {activeTab === "consensus" ? (
            <Column
              title={DIVERGENCE_MAP_BUCKET_LABELS.consensus}
              bucket={consensus}
              onSelect={setDrawerEntry}
              testId="divergence-map-tab-consensus"
            />
          ) : null}
          {activeTab === "b" ? (
            <Column
              title={`${data.reviewer_b}: ${DIVERGENCE_MAP_BUCKET_LABELS.additive_B}`}
              bucket={additiveB}
              onSelect={setDrawerEntry}
              testId="divergence-map-tab-b"
            />
          ) : null}
        </div>
      </div>

      {/* Three-column desktop layout. Visible at md and above. */}
      <div
        className="hidden md:grid md:grid-cols-3 md:gap-4"
        data-testid="divergence-map-grid"
      >
        <Column
          title={`${data.reviewer_a}: ${DIVERGENCE_MAP_BUCKET_LABELS.additive_A}`}
          bucket={additiveA}
          onSelect={setDrawerEntry}
          testId="divergence-map-column-a"
        />
        <Column
          title={DIVERGENCE_MAP_BUCKET_LABELS.consensus}
          bucket={consensus}
          onSelect={setDrawerEntry}
          testId="divergence-map-column-consensus"
        />
        <Column
          title={`${data.reviewer_b}: ${DIVERGENCE_MAP_BUCKET_LABELS.additive_B}`}
          bucket={additiveB}
          onSelect={setDrawerEntry}
          testId="divergence-map-column-b"
        />
      </div>

      {/* Contradictory items render across the full width with a CSS connector. */}
      {contradictory && contradictory.entries.length > 0 ? (
        <section
          data-testid="divergence-map-contradictory"
          className="rounded border border-amber-300 bg-amber-50 p-3"
        >
          <h3 className="text-sm font-medium text-amber-900">
            {DIVERGENCE_MAP_BUCKET_LABELS.contradictory}
          </h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {contradictory.entries.map((entry) => (
              <li
                key={`${entry.element_type}:${entry.element_name}`}
                className="flex items-center gap-2"
              >
                <span className="text-sm">
                  {entry.element_name}{" "}
                  <span className="text-slate-500">({entry.element_type})</span>
                </span>
                <button
                  type="button"
                  data-testid="divergence-map-evidence-badge"
                  onClick={() => setDrawerEntry(entry)}
                >
                  <Badge variant="secondary">
                    {DIVERGENCE_MAP_EVIDENCE_BADGE(entry.instance_count)}
                  </Badge>
                </button>
                {/* Source-origins badges (Chunk 60, CP8) */}
                {entry.source_origins && entry.source_origins.length > 0 && (
                  <span className="flex gap-0.5" data-testid="source-origins-badges">
                    {entry.source_origins.map((o) => (
                      <Badge key={o} variant="outline" className="text-[10px]">{o}</Badge>
                    ))}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <Dialog
        open={drawerEntry !== null}
        onOpenChange={(open) => !open && setDrawerEntry(null)}
      >
        <DialogContent data-testid="divergence-map-drawer">
          <DialogHeader>
            <DialogTitle>{DIVERGENCE_MAP_DRAWER_TITLE}</DialogTitle>
            <DialogDescription>
              {drawerEntry?.element_name ?? ""}
            </DialogDescription>
          </DialogHeader>
          {drawerEntry ? (
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-slate-500">Type</dt>
              <dd>{drawerEntry.element_type}</dd>
              <dt className="text-slate-500">Instances</dt>
              <dd>{drawerEntry.instance_count}</dd>
            </dl>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

type ColumnProps = {
  title: string;
  bucket: DivergenceMapBucket | undefined;
  onSelect: (entry: DivergenceMapEntry) => void;
  testId: string;
};

function Column({ title, bucket, onSelect, testId }: ColumnProps) {
  return (
    <div data-testid={testId} className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {bucket && bucket.entries.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {bucket.entries.map((entry) => (
            <li
              key={`${entry.element_type}:${entry.element_name}`}
              className="flex items-center justify-between gap-2 rounded border border-slate-200 px-2 py-1"
            >
              <span className="text-sm">
                {entry.element_name}{" "}
                <span className="text-slate-500">({entry.element_type})</span>
              </span>
              <button
                type="button"
                data-testid="divergence-map-evidence-badge"
                onClick={() => onSelect(entry)}
              >
                <Badge variant="secondary">
                  {DIVERGENCE_MAP_EVIDENCE_BADGE(entry.instance_count)}
                </Badge>
              </button>
              {/* Source-origins badges (Chunk 60, CP8) */}
              {entry.source_origins && entry.source_origins.length > 0 && (
                <span className="flex gap-0.5" data-testid="source-origins-badges">
                  {entry.source_origins.map((o) => (
                    <Badge key={o} variant="outline" className="text-[10px]">{o}</Badge>
                  ))}
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-400">—</p>
      )}
    </div>
  );
}
