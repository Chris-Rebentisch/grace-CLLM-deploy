import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OAuthPasteField } from "@/components/ingestion/OAuthPasteField";

describe("OAuthPasteField", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders init button", () => {
    render(
      <OAuthPasteField sourceId="test-id" provider="exchange" />,
    );
    expect(screen.getByText(/Start OAuth flow/i)).toBeTruthy();
  });

  it("fetches authorize URL on init click", async () => {
    const mockResp = {
      ok: true,
      json: async () => ({
        authorize_url: "https://login.example.com/auth?state=abc",
        state: "abc",
      }),
    };
    global.fetch = vi.fn().mockResolvedValue(mockResp);

    render(
      <OAuthPasteField sourceId="test-id" provider="exchange" />,
    );
    fireEvent.click(screen.getByText(/Start OAuth flow/i));

    await waitFor(() => {
      expect(screen.getByText(/Submit authorization/i)).toBeTruthy();
    });
  });

  it("shows error on init failure", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false });

    render(
      <OAuthPasteField sourceId="test-id" provider="gmail" />,
    );
    fireEvent.click(screen.getByText(/Start OAuth flow/i));

    await waitFor(() => {
      expect(screen.getByText(/Failed to start OAuth flow/i)).toBeTruthy();
    });
  });
});
