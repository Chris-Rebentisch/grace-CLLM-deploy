import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { RoleClusterMemberCardSort } from "@/components/permissions/RoleClusterMemberCardSort";

const clusters = [
  {
    cluster_id: "c-1",
    display_name: "Finance",
    member_grace_ids: ["p-1", "p-2"],
    hypothesis_confidence_band: "strong" as const,
  },
  {
    cluster_id: "c-2",
    display_name: "Engineering",
    member_grace_ids: [],
    hypothesis_confidence_band: "moderate" as const,
  },
];

describe("<RoleClusterMemberCardSort />", () => {
  it("renders one column per cluster including empty ones", () => {
    render(
      <RoleClusterMemberCardSort clusters={clusters} onMoveMember={() => {}} />,
    );
    expect(
      screen.getByTestId("role-cluster-member-column-c-1"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("role-cluster-member-column-c-2"),
    ).toBeInTheDocument();
    expect(screen.getByText("No members assigned")).toBeInTheDocument();
  });

  it("renders one row per member", () => {
    render(
      <RoleClusterMemberCardSort clusters={clusters} onMoveMember={() => {}} />,
    );
    expect(
      screen.getByTestId("role-cluster-member-row-c-1-p-1"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("role-cluster-member-row-c-1-p-2"),
    ).toBeInTheDocument();
  });

  it("forwards onMoveMember with from + to + member ids", () => {
    const onMoveMember = vi.fn();
    render(
      <RoleClusterMemberCardSort
        clusters={clusters}
        onMoveMember={onMoveMember}
      />,
    );
    fireEvent.click(screen.getByTestId("role-cluster-member-move-trigger-p-1"));
    fireEvent.click(
      screen.getByTestId("role-cluster-member-move-target-p-1-c-2"),
    );
    expect(onMoveMember).toHaveBeenCalledWith("p-1", "c-1", "c-2");
  });
});
