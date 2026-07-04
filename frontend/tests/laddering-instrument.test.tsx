import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LadderingInstrument } from "@/components/instruments/LadderingInstrument";

vi.mock("@/lib/telemetry/bus", () => ({ emitTelemetry: vi.fn(), onTelemetry: vi.fn() }));

describe("LadderingInstrument", () => {
  it("complete action emits laddering_step_completed", async () => {
    const { emitTelemetry } = await import("@/lib/telemetry/bus");
    const onComplete = vi.fn();
    render(<LadderingInstrument parentId="hash-abc" onComplete={onComplete} />);
    fireEvent.click(screen.getByTestId("instrument-complete-Laddering"));
    expect(emitTelemetry).toHaveBeenCalledWith("laddering_step_completed", expect.objectContaining({ parent_grace_id_hash: "hash-abc" }));
    expect(onComplete).toHaveBeenCalled();
  });
});
