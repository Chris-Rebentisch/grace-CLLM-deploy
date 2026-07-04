import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ClaimSpanRenderer } from "@/components/provenance/ClaimSpanRenderer";
import type { ClaimSpan } from "@/lib/api/types";

function ui(children: React.ReactNode) {
  return <TooltipProvider>{children}</TooltipProvider>;
}

describe("ClaimSpanRenderer", () => {
  it("renders three bands with distinct visual markers at the correct positions", () => {
    const responseText =
      "Apple is a technology company. Gravity is always 9.8 m/s^2. Pluto might not be a planet.";
    const spans: ClaimSpan[] = [
      {
        text: "Apple is a technology company",
        sentence_indices: [0],
        start_char: 0,
        end_char: 29,
        certainty_band: "high",
        span_confidence: "high",
        supporting_grace_ids: ["aaaaaaaa-1111-4000-8000-000000000001"],
      },
      {
        text: "Gravity is always 9.8 m/s^2",
        sentence_indices: [1],
        start_char: 31,
        end_char: 58,
        certainty_band: "medium",
        span_confidence: "medium",
        supporting_grace_ids: [],
      },
      {
        text: "Pluto might not be a planet",
        sentence_indices: [2],
        start_char: 60,
        end_char: 87,
        certainty_band: "insufficient_evidence",
        span_confidence: "low",
        supporting_grace_ids: [],
      },
    ];

    render(
      ui(
        <ClaimSpanRenderer responseText={responseText} claimSpans={spans} />,
      ),
    );

    expect(screen.getByTestId("claim-span-trigger-high")).toBeInTheDocument();
    expect(screen.getByTestId("claim-span-trigger-medium")).toBeInTheDocument();
    expect(
      screen.getByTestId("claim-span-trigger-insufficient_evidence"),
    ).toBeInTheDocument();

    // D191: distinct marker classes per band (decoration style differs).
    const high = screen.getByTestId("claim-span-trigger-high");
    const med = screen.getByTestId("claim-span-trigger-medium");
    const low = screen.getByTestId("claim-span-trigger-insufficient_evidence");
    expect(high.className).not.toBe(med.className);
    expect(med.className).not.toBe(low.className);
  });

  it("falls back to substring match when start_char / end_char are null", () => {
    const responseText = "Water boils at 100C at sea level.";
    const spans: ClaimSpan[] = [
      {
        text: "Water boils at 100C",
        sentence_indices: [0],
        start_char: null,
        end_char: null,
        certainty_band: "high",
        span_confidence: "high",
        supporting_grace_ids: [],
      },
    ];
    render(
      ui(
        <ClaimSpanRenderer responseText={responseText} claimSpans={spans} />,
      ),
    );
    const trigger = screen.getByTestId("claim-span-trigger-high");
    expect(trigger.textContent).toBe("Water boils at 100C");
  });

  it("D120 audit: no numeric confidence scores appear in the rendered DOM", () => {
    const responseText = "The sky is blue.";
    const spans: ClaimSpan[] = [
      {
        text: "The sky is blue",
        sentence_indices: [0],
        start_char: 0,
        end_char: 15,
        certainty_band: "medium",
        span_confidence: "medium",
        supporting_grace_ids: ["aaaaaaaa-1111-4000-8000-000000000001"],
      },
    ];
    const { container } = render(
      ui(
        <ClaimSpanRenderer responseText={responseText} claimSpans={spans} />,
      ),
    );
    const text = container.textContent ?? "";
    // Reject decimal floats like 0.72, 0.85, 72% etc.
    expect(text).not.toMatch(/\b0\.\d+\b/);
    expect(text).not.toMatch(/\b\d{1,3}%/);
    // Reject known Pydantic field leaks.
    expect(text).not.toMatch(/confidence[_ ]?score/i);
  });
});
