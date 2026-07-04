import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CardSortInstrument } from "@/components/instruments/CardSortInstrument";

vi.mock("@/lib/telemetry/bus", () => ({ emitTelemetry: vi.fn(), onTelemetry: vi.fn() }));

describe("CardSortInstrument", () => {
  it("wrapper delegates to CardSortCanvas", () => {
    render(<CardSortInstrument cards={[{ id: "c1", text: "Test CQ", category: "domain" }]} />);
    expect(screen.getByTestId("card-sort-instrument")).toBeTruthy();
    expect(screen.getByTestId("card-sort-canvas")).toBeTruthy();
  });
});
