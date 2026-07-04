import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("@/lib/api/permissions", () => ({
  permissionsApi: {
    getHypothesisRun: vi.fn(),
    ratifyMatrix: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ run_id: "run-1" }),
}));

import PermissionsRatifyPage from "@/app/permissions/ratify/[run_id]/page";
import { permissionsApi } from "@/lib/api/permissions";

const sampleRun = {
  run_id: "run-1",
  evidence_id: "ev-1",
  status: "completed",
  clusters: [
    {
      cluster_id: "c-1",
      display_name: "Finance",
      member_grace_ids: ["p-1", "p-2"],
      hypothesis_confidence_band: "strong",
      sensitivity_tag: null,
    },
    {
      cluster_id: "c-2",
      display_name: "Engineering",
      member_grace_ids: ["p-3"],
      hypothesis_confidence_band: "moderate",
      sensitivity_tag: null,
    },
  ],
};

describe("PermissionsRatifyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads the run and renders cluster cards", async () => {
    (permissionsApi.getHypothesisRun as any).mockResolvedValue(sampleRun);
    render(<PermissionsRatifyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("role-cluster-card-c-1")).toBeInTheDocument();
      expect(screen.getByTestId("role-cluster-card-c-2")).toBeInTheDocument();
    });
  });

  it("opens the ratify dialog when the confirm button is clicked", async () => {
    (permissionsApi.getHypothesisRun as any).mockResolvedValue(sampleRun);
    render(<PermissionsRatifyPage />);
    await waitFor(() =>
      expect(screen.getByTestId("permissions-ratify-open-dialog")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("permissions-ratify-open-dialog"));
    expect(
      screen.getByTestId("permission-matrix-ratify-dialog"),
    ).toBeInTheDocument();
  });

  it("disables the confirm button when there are no clusters", async () => {
    (permissionsApi.getHypothesisRun as any).mockResolvedValue({
      ...sampleRun,
      clusters: [],
    });
    render(<PermissionsRatifyPage />);
    await waitFor(() => {
      const btn = screen.getByTestId(
        "permissions-ratify-open-dialog",
      ) as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    });
  });

  it("surfaces the API error when the run cannot load", async () => {
    (permissionsApi.getHypothesisRun as any).mockRejectedValue(
      new Error("not found"),
    );
    render(<PermissionsRatifyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("permissions-ratify-error")).toBeInTheDocument();
    });
  });
});
