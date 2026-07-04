import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DpiaSettingsPage from "@/app/communications/profiles/settings/page";
import { DpiaAttestationForm } from "@/components/communications/DpiaAttestationForm";
import { COMMUNICATIONS_COPY } from "@/lib/communications/copy";

afterEach(() => cleanup());

// Mock crypto.subtle for SHA-256 computation in test env
const mockDigest = vi.fn().mockResolvedValue(new ArrayBuffer(32));
Object.defineProperty(globalThis, "crypto", {
  value: {
    subtle: { digest: mockDigest },
  },
  writable: true,
});

describe("DPIA settings page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders page title and description", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          attestation_active: false,
          valid_until: null,
          signed_by: null,
        }),
    });

    render(<DpiaSettingsPage />);
    await waitFor(() => {
      expect(screen.getByText("Voice & Tone DPIA settings")).toBeDefined();
    });
  });

  it("shows inactive state when no attestation", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          attestation_active: false,
          valid_until: null,
          signed_by: null,
        }),
    });

    render(<DpiaSettingsPage />);
    await waitFor(() => {
      expect(screen.getByText("No active attestation")).toBeDefined();
      expect(screen.getByText("Aggregate mode")).toBeDefined();
    });
  });

  it("shows active state with valid_until", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          attestation_active: true,
          valid_until: "2027-05-19",
          signed_by: "Alice",
        }),
    });

    render(<DpiaSettingsPage />);
    await waitFor(() => {
      expect(screen.getByText("Attestation active")).toBeDefined();
      expect(
        screen.getByText("Individual mode (requires DPIA)")
      ).toBeDefined();
    });
  });

  it("renders empty state when fetch fails", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("network"));

    render(<DpiaSettingsPage />);
    await waitFor(() => {
      expect(
        screen.getByText("Manage DPIA attestation for individual-mode profiling.")
      ).toBeDefined();
    });
  });

  it("contains navigation link text from copy registry", () => {
    expect(COMMUNICATIONS_COPY.navLink).toBe("DPIA settings");
  });
});

describe("DpiaAttestationForm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders form fields", () => {
    render(<DpiaAttestationForm templateSha256={"a".repeat(64)} />);
    expect(screen.getByTestId("signer-name")).toBeDefined();
    expect(screen.getByTestId("signer-role")).toBeDefined();
    expect(screen.getByTestId("signing-date")).toBeDefined();
    expect(screen.getByTestId("submit-attestation")).toBeDefined();
  });

  it("shows success on 201", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      status: 201,
      json: () =>
        Promise.resolve({ path: "/data/dpia/att.md", valid_until: "2027-05-19" }),
    });

    render(<DpiaAttestationForm templateSha256={"a".repeat(64)} />);

    const nameInput = screen.getByTestId("signer-name");
    const roleInput = screen.getByTestId("signer-role");
    await userEvent.type(nameInput, "Alice");
    await userEvent.type(roleInput, "DPO");
    await userEvent.click(screen.getByTestId("submit-attestation"));

    await waitFor(() => {
      expect(screen.getByTestId("success-message")).toBeDefined();
    });
  });

  it("shows duplicate error on 409 already exists", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      status: 409,
      json: () => Promise.resolve({ detail: "attestation already exists for today" }),
    });

    render(<DpiaAttestationForm templateSha256={"a".repeat(64)} />);

    const nameInput = screen.getByTestId("signer-name");
    const roleInput = screen.getByTestId("signer-role");
    await userEvent.type(nameInput, "Alice");
    await userEvent.type(roleInput, "DPO");
    await userEvent.click(screen.getByTestId("submit-attestation"));

    await waitFor(() => {
      expect(screen.getByTestId("error-message")).toBeDefined();
    });
  });

  it("shows template-changed error on 409 template mismatch", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      status: 409,
      json: () =>
        Promise.resolve({ detail: "template changed; reload and re-sign" }),
    });

    render(<DpiaAttestationForm templateSha256={"a".repeat(64)} />);

    const nameInput = screen.getByTestId("signer-name");
    const roleInput = screen.getByTestId("signer-role");
    await userEvent.type(nameInput, "Alice");
    await userEvent.type(roleInput, "DPO");
    await userEvent.click(screen.getByTestId("submit-attestation"));

    await waitFor(() => {
      const errorEl = screen.getByTestId("error-message");
      expect(errorEl.textContent).toContain("template has changed");
    });
  });
});
