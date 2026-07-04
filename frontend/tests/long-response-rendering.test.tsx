import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ClaimSpanRenderer } from "@/components/provenance/ClaimSpanRenderer";
import type { ClaimSpan } from "@/lib/api/types";

describe("long-response rendering", () => {
  it("handles 15+ spans across overlapping/adjacent ranges at 768px viewport", () => {
    // Simulate a tablet viewport.
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 768,
    });
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 1024,
    });

    // Build a long response with a mix of strict, adjacent, and overlap-attempted spans.
    const sentence =
      "The Acme trust owns an interest in three operating subsidiaries.";
    const responseText = Array.from({ length: 20 }).map(() => sentence).join(" ");
    const bands = ["high", "medium", "low", "insufficient_evidence"] as const;

    const spans: ClaimSpan[] = [];
    // First: 16 non-overlapping sequential spans.
    for (let i = 0; i < 16; i++) {
      const start = i * (sentence.length + 1);
      const end = start + sentence.length;
      spans.push({
        text: sentence,
        sentence_indices: [i],
        start_char: start,
        end_char: end,
        certainty_band: bands[i % bands.length],
        span_confidence: "medium",
        supporting_grace_ids: Array.from({ length: (i % 5) + 1 }).map(
          (_, n) =>
            `aaaaaaaa-${i.toString().padStart(4, "0")}-4${n}00-8000-00000000000${n}`,
        ),
      });
    }
    // Two overlap attempts on the first sentence — should be resolved so
    // the first wins and the overlap is silently dropped.
    spans.push({
      text: "Acme trust owns",
      sentence_indices: [0],
      start_char: 4,
      end_char: 22,
      certainty_band: "medium",
      span_confidence: "medium",
      supporting_grace_ids: [],
    });
    spans.push({
      text: "operating subsidiaries",
      sentence_indices: [0],
      start_char: 40,
      end_char: 62,
      certainty_band: "low",
      span_confidence: "low",
      supporting_grace_ids: [],
    });

    const { container } = render(
      <TooltipProvider>
        <ClaimSpanRenderer responseText={responseText} claimSpans={spans} />
      </TooltipProvider>,
    );
    const body = container.querySelector('[data-testid="claim-span-body"]');
    expect(body).not.toBeNull();

    // Exactly 16 triggers should render (the 16 non-overlapping ones).
    // The two overlap attempts inside the first span get dropped.
    const triggers = container.querySelectorAll(
      "[data-testid^='claim-span-trigger-']",
    );
    expect(triggers.length).toBe(16);

    // All 16 must carry a band marker class.
    for (const t of Array.from(triggers)) {
      expect(t.className).toMatch(/decoration/);
    }

    // D120 audit: no numeric confidence score in the DOM.
    const dom = container.textContent ?? "";
    expect(dom).not.toMatch(/\b0\.\d+\b/);
    expect(dom).not.toMatch(/\b\d{1,3}%/);
  });
});
