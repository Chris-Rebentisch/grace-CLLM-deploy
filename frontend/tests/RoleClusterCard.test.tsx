import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { RoleClusterCard } from "@/components/permissions/RoleClusterCard";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";

const sampleCluster = {
  cluster_id: "c-1",
  display_name: "Finance ops",
  member_grace_ids: ["p-1", "p-2", "p-3"],
  hypothesis_confidence_band: "strong" as const,
  sensitivity_tag: "PII",
};

describe("<RoleClusterCard />", () => {
  it("renders display name, member count, and band label only", () => {
    render(<RoleClusterCard cluster={sampleCluster} />);
    expect(screen.getByText("Finance ops")).toBeInTheDocument();
    expect(screen.getByText("3 members")).toBeInTheDocument();
    expect(
      screen.getByTestId("hypothesis-confidence-band-c-1").textContent,
    ).toBe(PERMISSIONS_COPY.hypothesisConfidenceStrong);
    // D120/D217: no numeric confidence value should appear in the DOM.
    const text = screen.getByTestId("role-cluster-card-c-1").textContent ?? "";
    expect(/\b0\.\d+\b/.test(text)).toBe(false);
  });

  it("invokes onSelect when the title is clicked", () => {
    const onSelect = vi.fn();
    render(<RoleClusterCard cluster={sampleCluster} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("role-cluster-card-select-c-1"));
    expect(onSelect).toHaveBeenCalledWith("c-1");
  });

  it("singularizes the member count for one-member clusters", () => {
    render(
      <RoleClusterCard
        cluster={{ ...sampleCluster, member_grace_ids: ["only"] }}
      />,
    );
    expect(screen.getByText("1 member")).toBeInTheDocument();
  });
});
