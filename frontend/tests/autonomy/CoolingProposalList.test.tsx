import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CoolingProposalList } from "@/components/autonomy/CoolingProposalList";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
  getApiBaseUrl: () => "http://127.0.0.1:8000",
}));

const MOCK_COOLING_PROPOSALS = {
  items: [
    {
      id: "aaa-111",
      proposal_type: "auto",
      change_tier: 1,
      kgcl_command: "create class 'NewType'",
      status: "cooling",
      cooling_period_expires_at: new Date(
        Date.now() + 24 * 3_600_000,
      ).toISOString(),
      cooling_outcome: null,
    },
    {
      id: "bbb-222",
      proposal_type: "auto",
      change_tier: 2,
      kgcl_command: "create relationship 'has_part'",
      status: "cooling",
      cooling_period_expires_at: new Date(
        Date.now() + 1 * 3_600_000,
      ).toISOString(),
      cooling_outcome: null,
    },
  ],
  next_cursor: null,
};

describe("CoolingProposalList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders cooling proposals", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_COOLING_PROPOSALS,
    );

    render(<CoolingProposalList />);
    await waitFor(() => {
      expect(
        screen.getByTestId("cooling-proposal-aaa-111"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("cooling-proposal-bbb-222"),
    ).toBeInTheDocument();
  });

  it("renders empty state when no cooling proposals", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      items: [],
      next_cursor: null,
    });

    render(<CoolingProposalList />);
    await waitFor(() => {
      expect(screen.getByTestId("cooling-empty")).toBeInTheDocument();
    });
    expect(screen.getByText(AUTONOMY_COPY.coolingEmpty)).toBeInTheDocument();
  });

  it("confirm CTA calls correct endpoint", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(MOCK_COOLING_PROPOSALS) // initial load
      .mockResolvedValueOnce({ status: "applied" }); // confirm

    render(<CoolingProposalList />);
    await waitFor(() => {
      expect(screen.getByTestId("confirm-btn-aaa-111")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("confirm-btn-aaa-111"));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/ontology/daemon/aaa-111/confirm",
        { method: "POST" },
      );
    });
  });

  it("confirm removes proposal from list", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(MOCK_COOLING_PROPOSALS)
      .mockResolvedValueOnce({ status: "applied" });

    render(<CoolingProposalList />);
    await waitFor(() => {
      expect(
        screen.getByTestId("cooling-proposal-aaa-111"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("confirm-btn-aaa-111"));

    await waitFor(() => {
      expect(
        screen.queryByTestId("cooling-proposal-aaa-111"),
      ).not.toBeInTheDocument();
    });
    // Second proposal should remain.
    expect(
      screen.getByTestId("cooling-proposal-bbb-222"),
    ).toBeInTheDocument();
  });

  it("revert CTA opens rationale dialog", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_COOLING_PROPOSALS,
    );

    render(<CoolingProposalList />);
    await waitFor(() => {
      expect(screen.getByTestId("revert-btn-aaa-111")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("revert-btn-aaa-111"));
    expect(screen.getByTestId("revert-dialog")).toBeInTheDocument();
    expect(
      screen.getByText(AUTONOMY_COPY.coolingRevertDialogTitle),
    ).toBeInTheDocument();
  });

  it("revert dialog cancel closes dialog", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_COOLING_PROPOSALS,
    );

    render(<CoolingProposalList />);
    await waitFor(() => {
      expect(screen.getByTestId("revert-btn-aaa-111")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("revert-btn-aaa-111"));
    expect(screen.getByTestId("revert-dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("revert-cancel-btn"));
    expect(screen.queryByTestId("revert-dialog")).not.toBeInTheDocument();
  });

  it("revert submit calls endpoint with reason", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(MOCK_COOLING_PROPOSALS) // initial load
      .mockResolvedValueOnce({ status: "reverted" }); // revert

    render(<CoolingProposalList />);
    await waitFor(() => {
      expect(screen.getByTestId("revert-btn-aaa-111")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("revert-btn-aaa-111"));

    const byInput = screen.getByTestId("revert-by-input");
    const reasonInput = screen.getByTestId("revert-reason-input");
    fireEvent.change(byInput, { target: { value: "admin" } });
    fireEvent.change(reasonInput, { target: { value: "Too risky" } });

    fireEvent.click(screen.getByTestId("revert-submit-btn"));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/ontology/daemon/aaa-111/revert",
        {
          method: "POST",
          body: { reverted_by: "admin", reason: "Too risky" },
        },
      );
    });
  });
});
