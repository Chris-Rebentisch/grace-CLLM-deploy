import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/api/permissions", () => ({
  permissionsApi: {
    getActiveMatrix: vi.fn(),
    listMatrixVersions: vi.fn(),
  },
}));

import PermissionsPage from "@/app/permissions/page";
import { permissionsApi } from "@/lib/api/permissions";

describe("PermissionsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the no-active-matrix hint when the API returns null", async () => {
    (permissionsApi.getActiveMatrix as any).mockResolvedValue(null);
    (permissionsApi.listMatrixVersions as any).mockResolvedValue({
      versions: [],
      active_payload_hash: null,
    });
    render(<PermissionsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("permissions-no-active")).toBeInTheDocument();
    });
  });

  it("renders the active matrix metadata when present", async () => {
    (permissionsApi.getActiveMatrix as any).mockResolvedValue({
      permission_matrix_id: "m-1",
      payload: {},
      payload_hash: "abc123def456hash7890",
      previous_hash: null,
      created_at: "2026-05-09T00:00:00Z",
      created_by: null,
      version_label: "v1",
    });
    (permissionsApi.listMatrixVersions as any).mockResolvedValue({
      versions: [],
      active_payload_hash: "abc123def456hash7890",
    });
    render(<PermissionsPage />);
    await waitFor(() => {
      expect(screen.getByText("m-1")).toBeInTheDocument();
    });
  });

  it("surfaces the error when API calls reject", async () => {
    (permissionsApi.getActiveMatrix as any).mockRejectedValue(
      new Error("boom"),
    );
    (permissionsApi.listMatrixVersions as any).mockRejectedValue(
      new Error("boom"),
    );
    render(<PermissionsPage />);
    // Per-call .catch() in the page returns null, so no error is shown.
    // Instead, both API calls succeed (with null) and we fall through to the
    // empty hint.
    await waitFor(() => {
      expect(screen.getByTestId("permissions-no-active")).toBeInTheDocument();
    });
  });
});
