"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import type { SessionSummary } from "@/lib/api/types";
import { useBeforeunload } from "@/lib/close/use-beforeunload";

export type SummaryViewProps = {
  summary: SessionSummary;
  sessionClosed: boolean;
  onConfirmSave: (args: { finalSummary: SessionSummary; edited: boolean }) => Promise<void> | void;
  onReturnToChat: (args: { edited: boolean }) => Promise<void> | void;
  saving?: boolean;
};

export function SummaryView({
  summary,
  sessionClosed,
  onConfirmSave,
  onReturnToChat,
  saving,
}: SummaryViewProps) {
  const [editedText, setEditedText] = useState(summary.narrative);
  const [originalText, setOriginalText] = useState(summary.narrative);
  const [returnDiscardPending, setReturnDiscardPending] = useState(false);

  // If the upstream summary reloads (e.g. new close), reset the baseline.
  useEffect(() => {
    setEditedText(summary.narrative);
    setOriginalText(summary.narrative);
  }, [summary.narrative]);

  const edited = editedText !== originalText;
  const shouldWarn = edited && !sessionClosed;
  useBeforeunload(shouldWarn);

  const finalSummary = useMemo<SessionSummary>(
    () => ({ ...summary, narrative: editedText }),
    [summary, editedText],
  );

  async function handleConfirm() {
    await onConfirmSave({ finalSummary, edited });
    setOriginalText(editedText);
  }

  async function handleReturnClick() {
    if (edited && !returnDiscardPending) {
      setReturnDiscardPending(true);
      return;
    }
    setReturnDiscardPending(false);
    await onReturnToChat({ edited });
  }

  return (
    <section
      data-testid="summary-view"
      aria-label="Session summary"
      className="flex flex-col gap-3 rounded-xl border border-border bg-background p-4"
    >
      <header>
        <h2 className="text-sm font-semibold">Review session summary</h2>
        <p className="text-xs text-muted-foreground">
          Edit freely. Confirm and Save commits the narrative; Return to Chat
          keeps the session active.
        </p>
      </header>

      <Textarea
        data-testid="summary-textarea"
        value={editedText}
        onChange={(e) => setEditedText(e.target.value)}
        rows={8}
        aria-label="Session summary narrative"
        disabled={sessionClosed || saving}
      />

      <div className="grid grid-cols-1 gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <PlaceholderList label="Decisions recorded" items={summary.decisions_recorded} />
        <PlaceholderList label="CQs flipped state" items={summary.cqs_flipped_state} />
        <PlaceholderList label="Deferred items" items={summary.deferred_items} />
        <PlaceholderList label="Certainty band shifts" items={summary.certainty_band_shifts} />
      </div>

      <p className="text-[11px] text-muted-foreground">
        Structured decision / CQ / deferred-item slots populate in Chunk 29.
      </p>

      <Separator />

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          data-testid="confirm-save"
          onClick={handleConfirm}
          disabled={sessionClosed || saving}
        >
          Confirm and Save
        </Button>
        <Button
          type="button"
          variant="outline"
          data-testid="return-to-chat"
          onClick={handleReturnClick}
          disabled={sessionClosed || saving}
        >
          {returnDiscardPending ? "Discard edits & return" : "Return to Chat"}
        </Button>
        {shouldWarn ? (
          <span
            role="note"
            data-testid="summary-unsaved-indicator"
            className="text-xs text-muted-foreground"
          >
            Unsaved edits — a browser prompt will appear if you navigate away.
          </span>
        ) : null}
      </div>
    </section>
  );
}

function PlaceholderList({
  label,
  items,
}: {
  label: string;
  items: Record<string, unknown>[];
}) {
  return (
    <div>
      <p className="font-medium">{label}</p>
      {items.length === 0 ? (
        <p className="text-muted-foreground">(none in this chunk)</p>
      ) : (
        <ul className="list-inside list-disc">
          {items.map((it, i) => (
            <li key={i}>{JSON.stringify(it)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
