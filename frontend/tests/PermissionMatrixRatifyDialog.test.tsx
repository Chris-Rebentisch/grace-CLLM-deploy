import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { PermissionMatrixRatifyDialog } from "@/components/permissions/PermissionMatrixRatifyDialog";

vi.mock("@/lib/api/permissions", () => ({
  permissionsApi: {
    ratifyMatrix: vi.fn(async () => ({
      permission_matrix_id: "matrix-1",
      payload: {},
      payload_hash: "abc123def456",
      previous_hash: null,
      created_at: "2026-05-09T00:00:00Z",
      created_by: null,
      version_label: "v1",
    })),
  },
}));

import { permissionsApi } from "@/lib/api/permissions";

describe("<PermissionMatrixRatifyDialog />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders YAML preview and disables when closed", () => {
    const { rerender } = render(
      <PermissionMatrixRatifyDialog
        open={false}
        onClose={() => {}}
        matrix={{ role_clusters: [] }}
      />,
    );
    expect(screen.queryByTestId("permission-matrix-ratify-dialog")).toBeNull();
    rerender(
      <PermissionMatrixRatifyDialog
        open
        onClose={() => {}}
        matrix={{ role_clusters: [] }}
      />,
    );
    expect(
      screen.getByTestId("permission-matrix-ratify-dialog"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("permission-matrix-yaml-preview").textContent,
    ).toContain("role_clusters");
  });

  it("calls ratifyMatrix on confirm and forwards the result", async () => {
    const onRatified = vi.fn();
    const onClose = vi.fn();
    render(
      <PermissionMatrixRatifyDialog
        open
        onClose={onClose}
        matrix={{ role_clusters: [] }}
        versionLabel="v1"
        createdBy="alice"
        onRatified={onRatified}
      />,
    );
    fireEvent.click(screen.getByTestId("permission-matrix-ratify-confirm"));
    await waitFor(() => expect(onRatified).toHaveBeenCalled());
    expect(permissionsApi.ratifyMatrix).toHaveBeenCalledWith({
      matrix: { role_clusters: [] },
      created_by: "alice",
      version_label: "v1",
    });
    expect(onClose).toHaveBeenCalled();
  });
});
