import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ScopeIndicator } from "@/components/scope/ScopeIndicator";

describe("ScopeIndicator (D194)", () => {
  it("renders a read-only 'Scope: All' chip with an aria label and tooltip trigger", () => {
    render(
      <TooltipProvider>
        <ScopeIndicator />
      </TooltipProvider>,
    );
    const chip = screen.getByTestId("scope-indicator");
    expect(chip).toBeInTheDocument();
    expect(chip.textContent).toMatch(/Scope: All/);
    expect(chip.getAttribute("aria-label")).toMatch(/Graph scope: all/i);
  });
});
