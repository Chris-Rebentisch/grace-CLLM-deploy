import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PhaseBanner } from "@/components/session/PhaseBanner";
import { useSessionStore } from "@/lib/state/session-store";

beforeEach(() => {
  useSessionStore.getState().clearSession();
});

describe("PhaseBanner", () => {
  it("renders the current phase in a non-attention-stealing style (D197)", () => {
    useSessionStore.getState().startSession("open");
    const { rerender } = render(<PhaseBanner />);
    let banner = screen.getByTestId("phase-banner");
    expect(banner.textContent).toMatch(/Open phase active/i);
    expect(banner.dataset.phase).toBe("open");
    // D197: must not be role="alert" / role="dialog" etc.; role=status
    // is the polite live region.
    expect(banner.getAttribute("role")).toBe("status");
    expect(banner.getAttribute("aria-live")).toBe("polite");
    // Subtle styling: muted text class must be applied (structural check).
    expect(banner.className).toMatch(/text-muted-foreground/);

    useSessionStore.getState().enterPhase("close");
    rerender(<PhaseBanner />);
    banner = screen.getByTestId("phase-banner");
    expect(banner.textContent).toMatch(/Close phase active/i);
    expect(banner.dataset.phase).toBe("close");
  });
});
