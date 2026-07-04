"use client";
import { useMemo, useState } from "react";
import { ElementDecisionBar } from "./ElementDecisionBar";
import { FlagAsIntentionalChangeButton } from "./FlagAsIntentionalChangeButton";
import { ReviewProgressBar } from "./ReviewProgressBar";
import { FriendlyTypeCard, humanizeTypeName } from "./FriendlyTypeCard";
import type { ReviewElement } from "@/lib/api/types";

export type SchemaElementListProps = {
  sessionId: string;
  elements: Record<string, unknown>[];
};

const DECIDED = new Set([
  "approved",
  "auto_approved",
  "rejected",
  "renamed",
  "merged",
  "edited",
  "split",
  "redirected",
  "reclassified",
]);

// D522 session — plain-language review surface. Entity types render as calm
// confirmation cards (Yes / Skip + "Something's off?" drawer); the legacy
// nine-verb decision bar lives behind an "advanced" toggle for power users.
export function SchemaElementList({ sessionId, elements }: SchemaElementListProps) {
  const [advanced, setAdvanced] = useState(false);

  const entityTypes = useMemo(
    () => elements.filter((e) => e.element_type === "entity_type") as ReviewElement[],
    [elements],
  );
  const relationshipTypes = useMemo(
    () =>
      elements.filter((e) => e.element_type === "relationship_type") as ReviewElement[],
    [elements],
  );

  // Pending first, decided sink to the bottom — keeps the reviewer's eye on
  // what still needs a call without hiding what's done.
  const sortPendingFirst = (list: ReviewElement[]) =>
    [...list].sort((a, b) => {
      const ad = a.decision && DECIDED.has(a.decision) ? 1 : 0;
      const bd = b.decision && DECIDED.has(b.decision) ? 1 : 0;
      return ad - bd;
    });

  const sortedEntities = sortPendingFirst(entityTypes);
  const sortedRels = sortPendingFirst(relationshipTypes);
  const pendingCount = entityTypes.filter(
    (e) => !(e.decision && DECIDED.has(e.decision)),
  ).length;

  return (
    <div data-testid="schema-element-list">
      <ReviewProgressBar sessionId={sessionId} />

      {/* Calm framing — what this screen is, in the reviewer's terms. */}
      <div className="mb-3 mt-1 rounded-md bg-slate-50 p-3 text-sm text-slate-600">
        I went through your documents and grouped what I found into{" "}
        <strong>{entityTypes.length}</strong>{" "}
        {entityTypes.length === 1 ? "kind" : "kinds"} of things worth tracking.
        {pendingCount > 0 ? (
          <>
            {" "}
            <strong>{pendingCount}</strong> still{" "}
            {pendingCount === 1 ? "needs" : "need"} your okay — for each, just say{" "}
            <em>Yes, track this</em> or <em>Skip it</em>. Not sure about one? Tap{" "}
            <em>“Something’s off?”</em> and ask me.
          </>
        ) : (
          <> You’ve reviewed them all — nice work.</>
        )}
        <button
          type="button"
          data-testid="toggle-advanced-controls"
          onClick={() => setAdvanced((v) => !v)}
          className="ml-2 text-xs text-slate-400 underline-offset-2 hover:text-slate-700 hover:underline"
        >
          {advanced ? "Hide advanced controls" : "Advanced controls"}
        </button>
      </div>

      <h3 className="mb-2 text-sm font-semibold text-slate-700">Entity Types</h3>
      {sortedEntities.map((el) => {
        const name = String(el.element_name ?? el.name ?? "");
        return advanced ? (
          <div key={name} className="mb-2 rounded-md border border-border p-2">
            <div className="mb-1 text-xs font-medium">{name}</div>
            <div className="flex flex-wrap items-center gap-2">
              <ElementDecisionBar
                sessionId={sessionId}
                elementName={name}
                elementType="entity_type"
                currentDecision={el.decision as string | null}
              />
              <FlagAsIntentionalChangeButton sessionId={sessionId} elementName={name} />
            </div>
          </div>
        ) : (
          <FriendlyTypeCard key={name} sessionId={sessionId} element={el} />
        );
      })}

      <h3 className="mb-2 mt-5 text-sm font-semibold text-slate-700">
        Relationship Types
      </h3>
      <p className="mb-2 text-xs text-slate-500">
        How those things connect to each other. These usually follow from the choices
        above — skim them, or ask if anything looks wrong.
      </p>
      {sortedRels.map((el) => {
        const name = String(el.element_name ?? el.name ?? "");
        return advanced ? (
          <div key={name} className="mb-2 rounded-md border border-border p-2">
            <div className="mb-1 text-xs font-medium">{name}</div>
            <div className="flex flex-wrap items-center gap-2">
              <ElementDecisionBar
                sessionId={sessionId}
                elementName={name}
                elementType="relationship_type"
                currentDecision={el.decision as string | null}
              />
              <FlagAsIntentionalChangeButton sessionId={sessionId} elementName={name} />
            </div>
          </div>
        ) : (
          <FriendlyTypeCard key={name} sessionId={sessionId} element={el} />
        );
      })}
    </div>
  );
}

// Re-exported for convenience in tests / future callers.
export { humanizeTypeName };
