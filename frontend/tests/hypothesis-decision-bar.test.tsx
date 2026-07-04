// Chunk 41 D322 — HypothesisDecisionBar 5-action verification + EC-12 copy snapshot.

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { HypothesisDecisionBar } from "@/components/decomposition/HypothesisDecisionBar";

// EC-12 forbidden tokens (D281+D289). Should NEVER appear in UI copy.
const FORBIDDEN_TOKENS = [
  "drift",
  "reality gap",
  "mistake",
  "incorrect",
  "failure",
  "deficit",
  "blind spot",
  "wrong",
];

describe("HypothesisDecisionBar (Chunk 41 D322)", () => {
  it("renders all five decision buttons", () => {
    render(<HypothesisDecisionBar onDecide={() => {}} />);
    expect(screen.getByTestId("decision-accepted-segmented")).toBeTruthy();
    expect(screen.getByTestId("decision-accepted-null")).toBeTruthy();
    expect(screen.getByTestId("decision-rerun-finer")).toBeTruthy();
    expect(screen.getByTestId("decision-rerun-coarser")).toBeTruthy();
    expect(screen.getByTestId("decision-reject-all")).toBeTruthy();
  });

  it("fires onDecide for the four non-rejection paths", () => {
    const fn = vi.fn();
    render(<HypothesisDecisionBar onDecide={fn} />);
    fireEvent.click(screen.getByTestId("decision-accepted-segmented"));
    fireEvent.click(screen.getByTestId("decision-accepted-null"));
    fireEvent.click(screen.getByTestId("decision-rerun-finer"));
    fireEvent.click(screen.getByTestId("decision-rerun-coarser"));
    expect(fn).toHaveBeenCalledTimes(4);
    expect(fn.mock.calls[0][0]).toBe("accepted_segmented");
    expect(fn.mock.calls[1][0]).toBe("accepted_null");
    expect(fn.mock.calls[2][0]).toBe("rerun_finer");
    expect(fn.mock.calls[3][0]).toBe("rerun_coarser");
  });

  it("opens the rationale dialog for reject-all and emits rationale on submit", () => {
    const fn = vi.fn();
    render(<HypothesisDecisionBar onDecide={fn} />);
    fireEvent.click(screen.getByTestId("decision-reject-all"));
    const dialog = screen.getByTestId("reject-rationale-dialog");
    expect(dialog).toBeTruthy();
    const input = screen.getByTestId(
      "reject-rationale-input",
    ) as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "needs different lens" } });
    fireEvent.click(screen.getByTestId("reject-rationale-submit"));
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn.mock.calls[0][0]).toBe("reject_all_reformulate");
    expect(fn.mock.calls[0][1]).toBe("needs different lens");
  });

  it("EC-12 copy snapshot: no forbidden tokens in any visible button text", () => {
    const { container } = render(<HypothesisDecisionBar onDecide={() => {}} />);
    fireEvent.click(screen.getByTestId("decision-reject-all")); // expand dialog
    const text = (container.textContent ?? "").toLowerCase();
    for (const token of FORBIDDEN_TOKENS) {
      expect(text).not.toContain(token.toLowerCase());
    }
  });
});
