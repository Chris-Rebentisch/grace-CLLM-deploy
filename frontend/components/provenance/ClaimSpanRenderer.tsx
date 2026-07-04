"use client";

import type { ClaimSpan } from "@/lib/api/types";
import { CertaintyChip } from "./CertaintyChip";

export type ClaimSpanRendererProps = {
  responseText: string;
  claimSpans: ClaimSpan[];
};

type ResolvedSpan = {
  span: ClaimSpan;
  start: number;
  end: number;
};

// Resolve char offsets for each claim span. Prefer (start_char, end_char)
// when present; otherwise fall back to finding `text` in response_text.
function resolveSpans(
  responseText: string,
  spans: ClaimSpan[],
): ResolvedSpan[] {
  const resolved: ResolvedSpan[] = [];
  const used: Array<[number, number]> = [];

  for (const span of spans) {
    let start: number | null = null;
    let end: number | null = null;

    if (
      typeof span.start_char === "number" &&
      typeof span.end_char === "number" &&
      span.start_char >= 0 &&
      span.end_char > span.start_char &&
      span.end_char <= responseText.length
    ) {
      start = span.start_char;
      end = span.end_char;
    } else if (span.text) {
      // Walk forward to find the first occurrence that doesn't collide.
      let from = 0;
      while (from < responseText.length) {
        const idx = responseText.indexOf(span.text, from);
        if (idx === -1) break;
        const collides = used.some(
          ([a, b]) => !(idx + span.text.length <= a || idx >= b),
        );
        if (!collides) {
          start = idx;
          end = idx + span.text.length;
          break;
        }
        from = idx + 1;
      }
    }

    if (start !== null && end !== null) {
      used.push([start, end]);
      resolved.push({ span, start, end });
    }
  }

  // Sort by start offset.
  resolved.sort((a, b) => a.start - b.start);
  return resolved;
}

// Walk response text and emit segments. Overlapping spans are resolved
// greedily: once a region is claimed by one span, following spans cannot
// re-claim it (resolveSpans above enforces non-collision, but we guard
// again here for safety).
export function ClaimSpanRenderer({
  responseText,
  claimSpans,
}: ClaimSpanRendererProps) {
  const segments: Array<
    | { kind: "text"; content: string }
    | { kind: "span"; span: ClaimSpan; content: string }
  > = [];
  const resolved = resolveSpans(responseText, claimSpans);

  let cursor = 0;
  for (const entry of resolved) {
    if (entry.start < cursor) continue; // non-overlap guard
    if (entry.start > cursor) {
      segments.push({
        kind: "text",
        content: responseText.slice(cursor, entry.start),
      });
    }
    segments.push({
      kind: "span",
      span: entry.span,
      content: responseText.slice(entry.start, entry.end),
    });
    cursor = entry.end;
  }
  if (cursor < responseText.length) {
    segments.push({ kind: "text", content: responseText.slice(cursor) });
  }

  return (
    <p
      data-testid="claim-span-body"
      className="whitespace-pre-wrap text-sm leading-relaxed"
    >
      {segments.map((seg, i) => {
        if (seg.kind === "text") {
          return <span key={i}>{seg.content}</span>;
        }
        return (
          <CertaintyChip key={i} span={seg.span}>
            {seg.content}
          </CertaintyChip>
        );
      })}
    </p>
  );
}
