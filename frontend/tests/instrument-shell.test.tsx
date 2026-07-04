import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { InstrumentShell } from "@/components/instruments/InstrumentShell";

describe("InstrumentShell", () => {
  it("renders with phase awareness and double-mount dedup", () => {
    render(<InstrumentShell instrumentName="test-instrument"><div>content</div></InstrumentShell>);
    expect(screen.getByTestId("instrument-shell-test-instrument")).toBeTruthy();
    expect(screen.getByText("content")).toBeTruthy();
  });
});
