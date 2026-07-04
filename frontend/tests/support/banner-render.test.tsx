import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { SupportSessionBanner } from "@/components/support/SupportSessionBanner";
import { BANNER_COPY } from "@/lib/support/banner_copy";

// Mock the telemetry bus so we can verify event emission.
const emitSpy = vi.fn();
vi.mock("@/lib/telemetry/bus", () => ({
  emitTelemetry: (...args: unknown[]) => emitSpy(...args),
}));

function mockFetchResponse(body: Record<string, unknown>, ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    json: async () => body,
  });
}

describe("SupportSessionBanner", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    emitSpy.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders nothing when session is inactive", async () => {
    global.fetch = mockFetchResponse({ active: false, email: null, expires_at: null });

    render(<SupportSessionBanner />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());

    expect(screen.queryByTestId("support-session-banner")).toBeNull();
  });

  it("renders banner when session is active", async () => {
    global.fetch = mockFetchResponse({
      active: true,
      email: "op@example.com",
      expires_at: "2026-05-12T20:00:00Z",
    });

    render(<SupportSessionBanner />);
    await waitFor(() =>
      expect(screen.getByTestId("support-session-banner")).toBeInTheDocument(),
    );

    expect(screen.getByText(BANNER_COPY.ACTIVE_LABEL)).toBeInTheDocument();
    expect(screen.getByText(/op@example.com/)).toBeInTheDocument();
  });

  it("shows revoke button for admin users", async () => {
    global.fetch = mockFetchResponse({
      active: true,
      email: "op@example.com",
      expires_at: "2026-05-12T20:00:00Z",
    });

    render(<SupportSessionBanner isAdmin={true} />);
    await waitFor(() =>
      expect(screen.getByTestId("support-revoke-button")).toBeInTheDocument(),
    );

    expect(screen.getByText(BANNER_COPY.REVOKE_BUTTON)).toBeInTheDocument();
  });

  it("shows contact admin text for non-admin users", async () => {
    global.fetch = mockFetchResponse({
      active: true,
      email: "op@example.com",
      expires_at: "2026-05-12T20:00:00Z",
    });

    render(<SupportSessionBanner isAdmin={false} />);
    await waitFor(() =>
      expect(screen.getByTestId("support-session-banner")).toBeInTheDocument(),
    );

    expect(screen.getByText(BANNER_COPY.CONTACT_ADMIN)).toBeInTheDocument();
    expect(screen.queryByTestId("support-revoke-button")).toBeNull();
  });

  it("emits support_banner_viewed telemetry once", async () => {
    global.fetch = mockFetchResponse({
      active: true,
      email: "op@example.com",
      expires_at: "2026-05-12T20:00:00Z",
    });

    render(<SupportSessionBanner />);
    await waitFor(() =>
      expect(screen.getByTestId("support-session-banner")).toBeInTheDocument(),
    );

    expect(emitSpy).toHaveBeenCalledTimes(1);
    expect(emitSpy).toHaveBeenCalledWith(
      "support_banner_viewed",
      expect.objectContaining({
        session_email: "op@example.com",
      }),
    );
  });

  it("does not emit telemetry when inactive", async () => {
    global.fetch = mockFetchResponse({ active: false, email: null, expires_at: null });

    render(<SupportSessionBanner />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());

    expect(emitSpy).not.toHaveBeenCalled();
  });
});
