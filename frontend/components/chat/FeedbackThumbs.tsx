"use client";

/**
 * FeedbackThumbs — Chunk 35a (D266).
 *
 * Lightweight thumbs-up / thumbs-down control attached to an assistant
 * message. On vote-down, exposes an optional freetext field (max 2048
 * chars, mirroring the server CHECK constraint). Submission posts to
 * `POST /api/feedback/retrieval`; success state is a one-shot
 * acknowledgement that disables the controls.
 *
 * No DropdownMenu primitive is used (shadcn install limited to
 * button/dialog/textarea/etc.) — a native `<textarea>` keeps the
 * dependency surface unchanged.
 *
 * Styling: numeric scores never render here (D120/D217). The two
 * controls are textual labels; the optional success line is also text.
 */

import { useState } from "react";
import { apiClient } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { FeedbackVote } from "@/lib/api/types";

const FREETEXT_MAX = 2048;

export type FeedbackThumbsProps = {
  /** Correlation id for the underlying retrieval response. */
  queryEventId: string;
  /** Override the apiClient submitter (test seam). */
  onSubmit?: (body: {
    query_event_id: string;
    vote: FeedbackVote;
    freetext?: string;
  }) => Promise<unknown>;
};

type Status = "idle" | "down-pending" | "submitting" | "submitted" | "error";

export function FeedbackThumbs({ queryEventId, onSubmit }: FeedbackThumbsProps) {
  const [status, setStatus] = useState<Status>("idle");
  const [vote, setVote] = useState<FeedbackVote | null>(null);
  const [freetext, setFreetext] = useState("");

  const submit = onSubmit ?? apiClient.submitRetrievalFeedback;

  async function handleVote(nextVote: FeedbackVote) {
    if (status === "submitting" || status === "submitted") return;
    setVote(nextVote);
    if (nextVote === "down") {
      // Stage two: wait for the user to optionally add freetext, then submit.
      setStatus("down-pending");
      return;
    }
    await persist(nextVote, undefined);
  }

  async function handleSubmitDown() {
    if (vote !== "down") return;
    const trimmed = freetext.trim();
    await persist("down", trimmed.length > 0 ? trimmed : undefined);
  }

  async function persist(v: FeedbackVote, ft: string | undefined) {
    setStatus("submitting");
    try {
      await submit({
        query_event_id: queryEventId,
        vote: v,
        ...(ft !== undefined ? { freetext: ft } : {}),
      });
      setStatus("submitted");
    } catch {
      setStatus("error");
    }
  }

  const disabled =
    status === "submitting" || status === "submitted";

  if (status === "submitted") {
    return (
      <div
        className="mt-2 text-xs text-muted-foreground"
        data-testid="feedback-thumbs"
        data-status="submitted"
      >
        Thanks — feedback recorded.
      </div>
    );
  }

  return (
    <div
      className="mt-2 flex flex-col gap-2"
      data-testid="feedback-thumbs"
      data-status={status}
    >
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>Was this helpful?</span>
        <Button
          type="button"
          size="sm"
          variant={vote === "up" ? "secondary" : "ghost"}
          disabled={disabled}
          onClick={() => handleVote("up")}
          aria-label="Thumbs up"
          data-testid="feedback-thumbs-up"
        >
          Thumbs up
        </Button>
        <Button
          type="button"
          size="sm"
          variant={vote === "down" ? "secondary" : "ghost"}
          disabled={disabled}
          onClick={() => handleVote("down")}
          aria-label="Thumbs down"
          data-testid="feedback-thumbs-down"
        >
          Thumbs down
        </Button>
      </div>

      {status === "down-pending" && (
        <div
          className="flex flex-col gap-2"
          data-testid="feedback-thumbs-freetext-panel"
        >
          <Textarea
            value={freetext}
            onChange={(e) => setFreetext(e.target.value.slice(0, FREETEXT_MAX))}
            maxLength={FREETEXT_MAX}
            placeholder="Optional: tell us what was wrong (max 2048 chars)"
            data-testid="feedback-thumbs-freetext"
          />
          <div className="flex items-center justify-end">
            <Button
              type="button"
              size="sm"
              onClick={handleSubmitDown}
              disabled={disabled}
              data-testid="feedback-thumbs-submit"
            >
              Submit feedback
            </Button>
          </div>
        </div>
      )}

      {status === "error" && (
        <div
          className="text-xs text-destructive"
          data-testid="feedback-thumbs-error"
        >
          Couldn&apos;t record feedback. Try again later.
        </div>
      )}
    </div>
  );
}
