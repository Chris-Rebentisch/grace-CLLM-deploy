"use client";

import { useState } from "react";
import type { AssistantChatMessage } from "@/lib/state/chat-store";
import { Separator } from "@/components/ui/separator";

export type SourcePanelProps = {
  message: AssistantChatMessage;
};

// Collapsible "Why this answer" panel. Exposes provenance + retrieval
// metadata. D120: does not display numeric confidence — latency/token
// counts are operational, not confidence.
export function SourcePanel({ message }: SourcePanelProps) {
  const [open, setOpen] = useState(false);
  const contributingCount = message.claim_spans.reduce(
    (total, s) => total + s.supporting_grace_ids.length,
    0,
  );
  const strategies = message.strategy_contributions ?? {};

  return (
    <div
      data-testid="source-panel"
      className="mt-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-xs"
    >
      <button
        type="button"
        data-testid="source-panel-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between font-medium"
      >
        Why this answer
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open ? (
        <div className="mt-2 space-y-2" data-testid="source-panel-body">
          <section aria-label="Model">
            <p className="text-muted-foreground">Model &amp; provider</p>
            <p className="font-mono text-[11px]" data-testid="source-panel-model">
              {message.model ?? "unknown"} • {message.provider ?? "unknown"}
            </p>
          </section>
          <Separator />
          <section aria-label="Evidence">
            <p className="text-muted-foreground">Evidence</p>
            <p data-testid="source-panel-evidence-count">
              {contributingCount === 0
                ? "No supporting entities were attached to this answer."
                : `${contributingCount} supporting reference${contributingCount === 1 ? "" : "s"} across ${message.claim_spans.length} span${message.claim_spans.length === 1 ? "" : "s"}.`}
            </p>
          </section>
          {Object.keys(strategies).length > 0 ? (
            <>
              <Separator />
              <section aria-label="Retrieval strategy">
                <p className="text-muted-foreground">Retrieval strategies</p>
                <ul
                  className="mt-1 flex flex-wrap gap-1"
                  data-testid="source-panel-strategies"
                >
                  {Object.entries(strategies).map(([name, count]) => (
                    <li
                      key={name}
                      className="rounded border border-border bg-background px-1 py-0.5 font-mono"
                    >
                      {name}: {count}
                    </li>
                  ))}
                </ul>
              </section>
            </>
          ) : null}
          <p className="text-muted-foreground">
            Full retrieval inspector arrives in Chunk 28.
          </p>
        </div>
      ) : null}
    </div>
  );
}
