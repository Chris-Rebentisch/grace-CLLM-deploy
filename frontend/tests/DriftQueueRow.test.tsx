import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { DriftQueueRow } from "@/components/permissions/DriftQueueRow";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";

const baseRow = {
  drift_queue_id: "dq-1",
  person_grace_id: "person-1",
  proposed_cluster_id: "cluster-7",
  drift_band: "high" as const,
  status: "pending" as const,
  rationale: PERMISSIONS_COPY.driftRationaleHigh,
  auto_assigned: true,
  created_at: "2026-05-09T00:00:00Z",
};

describe("<DriftQueueRow />", () => {
  it("renders the band label and rationale (no numerics)", () => {
    render(<DriftQueueRow row={baseRow} />);
    expect(screen.getByTestId("drift-queue-band-dq-1").textContent).toBe(
      PERMISSIONS_COPY.driftBandHigh,
    );
    const text = screen.getByTestId("drift-queue-row-dq-1").textContent ?? "";
    expect(/\b0\.\d+\b/.test(text)).toBe(false);
  });

  it("does not render inert decide buttons without onDecide", () => {
    render(<DriftQueueRow row={baseRow} />);
    expect(
      screen.queryByTestId("drift-queue-accept-dq-1"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("drift-queue-pending-runbook-dq-1")).toHaveTextContent(
      PERMISSIONS_COPY.driftQueueRunbookHint,
    );
  });

  it("invokes onDecide with the chosen verdict", () => {
    const onDecide = vi.fn();
    render(<DriftQueueRow row={baseRow} onDecide={onDecide} />);
    fireEvent.click(screen.getByTestId("drift-queue-accept-dq-1"));
    expect(onDecide).toHaveBeenCalledWith(baseRow, "accept");
    fireEvent.click(screen.getByTestId("drift-queue-defer-dq-1"));
    expect(onDecide).toHaveBeenCalledWith(baseRow, "defer");
    fireEvent.click(screen.getByTestId("drift-queue-reject-dq-1"));
    expect(onDecide).toHaveBeenCalledWith(baseRow, "reject");
  });
});
