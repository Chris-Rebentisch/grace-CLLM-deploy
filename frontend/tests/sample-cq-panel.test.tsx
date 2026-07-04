// Chunk 41 D324 — SampleCqPanel cq_type label + approve/reject toggle.

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { SampleCqPanel, type SampleCq } from "@/components/decomposition/SampleCqPanel";

const FIXTURE: SampleCq[] = [
  { question: "Who owns the budget?", cq_type: "ownership" },
  { question: "When does the contract renew?", cq_type: "temporal" },
];

describe("SampleCqPanel (Chunk 41 D324)", () => {
  it("renders the cq_type label for each candidate", () => {
    render(<SampleCqPanel segmentName="finance" cqs={FIXTURE} />);
    const labels = screen.getAllByTestId("sample-cq-type-finance");
    expect(labels.length).toBe(2);
    const text = labels.map((el) => el.textContent ?? "").join(" ");
    expect(text).toContain("ownership");
    expect(text).toContain("temporal");
  });

  it("approve / reject toggles aria-pressed and emits decisions to onChange", () => {
    const fn = vi.fn();
    render(<SampleCqPanel segmentName="finance" cqs={FIXTURE} onChange={fn} />);
    const approveBtns = screen.getAllByTestId("sample-cq-approve-finance");
    fireEvent.click(approveBtns[0]);
    expect(approveBtns[0].getAttribute("aria-pressed")).toBe("true");
    expect(fn).toHaveBeenCalled();
    const lastCall = fn.mock.calls[fn.mock.calls.length - 1];
    const segName = lastCall[0] as string;
    expect(segName).toBe("finance");
  });

  it("renders the empty state when no candidates returned", () => {
    render(<SampleCqPanel segmentName="empty-seg" cqs={[]} />);
    expect(screen.getByTestId("sample-cq-empty-empty-seg")).toBeTruthy();
  });
});
