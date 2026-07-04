import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TeachBackInstrument } from "@/components/instruments/TeachBackInstrument";

vi.mock("@/lib/telemetry/bus", () => ({ emitTelemetry: vi.fn(), onTelemetry: vi.fn() }));

const ITEMS = [
  { index: 0, sentence: "The company is a legal entity." },
  { index: 1, sentence: "Contracts bind two parties." },
];

describe("TeachBackInstrument", () => {
  it("radiogroup ARIA pattern verified", () => {
    render(<TeachBackInstrument items={ITEMS} />);
    const groups = screen.getAllByTestId("teach-back-radiogroup");
    expect(groups.length).toBeGreaterThan(0);
    expect(groups[0].getAttribute("role")).toBe("radiogroup");
  });

  it("textarea reveals on wrong/missing-something selection", () => {
    render(<TeachBackInstrument items={ITEMS} />);
    // Initially no textarea
    expect(screen.queryByTestId("teach-back-textarea-0")).toBeNull();
    // Select "wrong" for first item
    const radios = screen.getAllByRole("radio");
    const wrongRadio = radios.find((r) => (r as HTMLInputElement).value === "wrong");
    if (wrongRadio) fireEvent.click(wrongRadio);
    expect(screen.getByTestId("teach-back-textarea-0")).toBeTruthy();
  });

  it("complete submits all labels", async () => {
    const { emitTelemetry } = await import("@/lib/telemetry/bus");
    render(<TeachBackInstrument items={ITEMS} />);
    fireEvent.click(screen.getByTestId("teach-back-complete"));
    expect(emitTelemetry).toHaveBeenCalledWith("teach_back_completed", expect.objectContaining({ sentence_count: 2 }));
  });
});
