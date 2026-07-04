import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/lib/api/sensitivity", () => ({
  sensitivityApi: {
    listAuditTrail: vi.fn(),
  },
}));

import { SensitivityAuditTrailFilter } from "@/components/sensitivity/SensitivityAuditTrailFilter";
import { sensitivityApi } from "@/lib/api/sensitivity";

describe("SensitivityAuditTrailFilter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the runbook hint and filter prompt", () => {
    render(<SensitivityAuditTrailFilter matrixId="m-1" />);
    expect(
      screen.getByTestId("sensitivity-audit-trail-runbook-hint"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("sensitivity-audit-trail-tag-input"),
    ).toBeInTheDocument();
  });

  it("requires a non-empty tag before applying", async () => {
    const user = userEvent.setup();
    render(<SensitivityAuditTrailFilter matrixId="m-1" />);
    await user.click(screen.getByTestId("sensitivity-audit-trail-apply"));
    expect(
      screen.getByTestId("sensitivity-audit-trail-error"),
    ).toBeInTheDocument();
    expect(sensitivityApi.listAuditTrail as any).not.toHaveBeenCalled();
  });

  it("invokes the API with the trimmed tag and matrixId", async () => {
    (sensitivityApi.listAuditTrail as any).mockResolvedValue({
      events: [],
      next_cursor: null,
    });
    const user = userEvent.setup();
    render(<SensitivityAuditTrailFilter matrixId="m-1" />);
    await user.type(
      screen.getByTestId("sensitivity-audit-trail-tag-input"),
      "  pii  ",
    );
    await user.click(screen.getByTestId("sensitivity-audit-trail-apply"));
    await waitFor(() => {
      expect(sensitivityApi.listAuditTrail as any).toHaveBeenCalledWith({
        tag: "pii",
        matrixId: "m-1",
      });
    });
  });

  it("renders the empty state when API returns zero events", async () => {
    (sensitivityApi.listAuditTrail as any).mockResolvedValue({
      events: [],
      next_cursor: null,
    });
    const user = userEvent.setup();
    render(<SensitivityAuditTrailFilter matrixId="m-1" />);
    await user.type(
      screen.getByTestId("sensitivity-audit-trail-tag-input"),
      "pii",
    );
    await user.click(screen.getByTestId("sensitivity-audit-trail-apply"));
    await waitFor(() => {
      expect(
        screen.getByTestId("sensitivity-audit-trail-empty"),
      ).toBeInTheDocument();
    });
  });

  it("renders one row per result when API returns events", async () => {
    (sensitivityApi.listAuditTrail as any).mockResolvedValue({
      events: [
        {
          query_event_id: "qe-1",
          occurred_at: "2026-05-09T00:00:00Z",
          sensitivity_tags: ["pii"],
        },
        {
          query_event_id: "qe-2",
          occurred_at: "2026-05-09T00:01:00Z",
          sensitivity_tags: ["pii", "phi"],
        },
      ],
      next_cursor: null,
    });
    const user = userEvent.setup();
    render(<SensitivityAuditTrailFilter matrixId="m-1" />);
    await user.type(
      screen.getByTestId("sensitivity-audit-trail-tag-input"),
      "pii",
    );
    await user.click(screen.getByTestId("sensitivity-audit-trail-apply"));
    await waitFor(() => {
      expect(
        screen.getByTestId("sensitivity-audit-trail-row-qe-1"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("sensitivity-audit-trail-row-qe-2"),
      ).toBeInTheDocument();
    });
  });
});
