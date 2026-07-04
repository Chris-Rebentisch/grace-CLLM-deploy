import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { RoleClusterMemberMoveMenu } from "@/components/permissions/RoleClusterMemberMoveMenu";

const clusters = [
  {
    cluster_id: "c-1",
    display_name: "Finance",
    member_grace_ids: ["p-1"],
    hypothesis_confidence_band: "strong" as const,
  },
  {
    cluster_id: "c-2",
    display_name: "Engineering",
    member_grace_ids: ["p-2"],
    hypothesis_confidence_band: "moderate" as const,
  },
];

describe("<RoleClusterMemberMoveMenu />", () => {
  it("opens the menu and shows targets that are not the source cluster", () => {
    render(
      <RoleClusterMemberMoveMenu
        memberGraceId="p-1"
        fromClusterId="c-1"
        clusters={clusters}
        onMove={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("role-cluster-member-move-trigger-p-1"));
    expect(
      screen.getByTestId("role-cluster-member-move-target-p-1-c-2"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("role-cluster-member-move-target-p-1-c-1"),
    ).toBeNull();
  });

  it("calls onMove with the clicked target", () => {
    const onMove = vi.fn();
    render(
      <RoleClusterMemberMoveMenu
        memberGraceId="p-1"
        fromClusterId="c-1"
        clusters={clusters}
        onMove={onMove}
      />,
    );
    fireEvent.click(screen.getByTestId("role-cluster-member-move-trigger-p-1"));
    fireEvent.click(
      screen.getByTestId("role-cluster-member-move-target-p-1-c-2"),
    );
    expect(onMove).toHaveBeenCalledWith("p-1", "c-2");
  });
});
