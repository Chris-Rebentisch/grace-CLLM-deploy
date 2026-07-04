"use client";
import { useState } from "react";
import { useDecide } from "@/lib/query/review";
import type { ReviewElement } from "@/lib/api/types";
import { ReviewAssistDrawer } from "./ReviewAssistDrawer";

// D522 session — plain-language confirmation card for one proposed "kind of thing".
// Replaces the nine-verb decision bar as the DEFAULT surface so a non-technical
// reviewer (a CFO) can confirm types without graph knowledge. The modeling verbs
// (split/merge/reclassify/...) move into the conversational drawer.

/** Humanize a technical type name as a fallback when the LLM gave no display_label. */
export function humanizeTypeName(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

/** Map the UI element_type to the backend ReviewElementType enum value. */
function backendElementType(elementType: string): string {
  return elementType === "entity_type" ? "entity_type" : "relationship";
}

export type FriendlyTypeCardProps = {
  sessionId: string;
  element: ReviewElement;
};

export function FriendlyTypeCard({ sessionId, element }: FriendlyTypeCardProps) {
  const decide = useDecide(sessionId);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const name = element.element_name ?? element.name ?? "";
  const label = element.display_label?.trim() || humanizeTypeName(name);
  const blurb =
    element.plain_description?.trim() || element.description?.trim() || "";
  const questions = element.answerable_questions ?? [];
  const docCount = element.evidence_document_count ?? 0;
  const decision = element.decision;
  const elementTypeForApi = backendElementType(element.element_type);

  const post = (payload: Record<string, unknown>) =>
    decide.mutate({ element_type: elementTypeForApi, element_name: name, ...payload });

  const decided = decision === "approved" || decision === "auto_approved";
  const skipped = decision === "rejected";

  return (
    <div
      data-testid={`friendly-card-${name}`}
      data-decision={decision ?? "pending"}
      className="mb-3 rounded-lg border border-border bg-white p-4 shadow-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-base font-semibold text-slate-900">{label}</div>
          {/* Technical name kept visible but de-emphasized (and for test stability). */}
          <div className="mt-0.5 font-mono text-[11px] text-slate-400">{name}</div>
        </div>
        {docCount > 0 ? (
          <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
            Seen in {docCount} {docCount === 1 ? "document" : "documents"}
          </span>
        ) : null}
      </div>

      {blurb ? <p className="mt-2 text-sm text-slate-700">{blurb}</p> : null}

      {element.example_snippet ? (
        <p className="mt-2 border-l-2 border-slate-200 pl-3 text-sm italic text-slate-500">
          “{element.example_snippet}”
        </p>
      ) : null}

      {questions.length > 0 ? (
        <div className="mt-3">
          <div className="text-xs font-medium text-slate-500">
            Helps answer questions you care about:
          </div>
          <ul className="mt-1 list-disc pl-5 text-sm text-slate-700">
            {questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {decided ? (
          <span
            data-testid={`card-status-${name}`}
            className="rounded-md bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700"
          >
            ✓ Tracking this
          </span>
        ) : skipped ? (
          <span
            data-testid={`card-status-${name}`}
            className="rounded-md bg-slate-100 px-3 py-1.5 text-sm font-medium text-slate-500"
          >
            Skipped
          </span>
        ) : (
          <>
            {/* Keep the testid the integration test relies on; "Yes" == approve. */}
            <button
              type="button"
              data-testid={`decision-btn-approved-${name}`}
              onClick={() => post({ decision: "approved" })}
              disabled={decide.isPending}
              className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              Yes, track this
            </button>
            <button
              type="button"
              data-testid={`decision-btn-rejected-${name}`}
              onClick={() => post({ decision: "rejected" })}
              disabled={decide.isPending}
              className="rounded-md bg-slate-100 px-4 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-50"
            >
              Skip it
            </button>
          </>
        )}

        <button
          type="button"
          data-testid={`assist-trigger-${name}`}
          onClick={() => setDrawerOpen(true)}
          className="ml-auto text-sm text-slate-500 underline-offset-2 hover:text-slate-800 hover:underline"
        >
          {decided || skipped ? "Change my mind →" : "Something's off? →"}
        </button>
      </div>

      <ReviewAssistDrawer
        sessionId={sessionId}
        element={element}
        elementTypeForApi={elementTypeForApi}
        friendlyLabel={label}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
