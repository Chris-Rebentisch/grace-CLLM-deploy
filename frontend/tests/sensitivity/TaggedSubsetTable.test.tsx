import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaggedSubsetTable } from "@/components/sensitivity/TaggedSubsetTable";
import type { TaggedSubset } from "@/lib/api/types";

const baseSubset: TaggedSubset = {
  matrix_schema_version: "1.0",
  cluster_decisions: [
    {
      cluster_id: "c1",
      cluster_display_name: "Finance ops",
      resource_kind: "ontology_module",
      resource_label: "finance",
      action: "view",
      decision: "allow",
      sensitivity_tags: [{ name: "pii", framework_mappings: [] }],
    },
    {
      cluster_id: "c2",
      cluster_display_name: "Auditors",
      resource_kind: "segment",
      resource_label: "audit",
      action: "edit",
      decision: "deny",
      sensitivity_tags: [
        { name: "pii", framework_mappings: [] },
        { name: "phi", framework_mappings: [] },
      ],
    },
  ],
};

describe("TaggedSubsetTable", () => {
  it("renders a row per cluster decision with cluster + tags + decision", () => {
    render(<TaggedSubsetTable subset={baseSubset} />);
    expect(screen.getByTestId("tagged-subset-table")).toBeInTheDocument();
    expect(screen.getByText("Finance ops")).toBeInTheDocument();
    expect(screen.getByText("Auditors")).toBeInTheDocument();
  });

  it("renders the empty state when there are no tagged decisions", () => {
    render(
      <TaggedSubsetTable
        subset={{ matrix_schema_version: "1.0", cluster_decisions: [] }}
      />,
    );
    expect(screen.getByTestId("tagged-subset-empty")).toBeInTheDocument();
  });

  it("renders one tag chip per sensitivity tag", () => {
    render(<TaggedSubsetTable subset={baseSubset} />);
    expect(screen.getAllByTestId("tagged-subset-tag-pii").length).toBe(2);
    expect(screen.getAllByTestId("tagged-subset-tag-phi").length).toBe(1);
  });

  it("colors allow vs deny decisions distinctly", () => {
    render(<TaggedSubsetTable subset={baseSubset} />);
    const allowCells = screen.getAllByText("allow");
    const denyCells = screen.getAllByText("deny");
    expect(allowCells.length).toBe(1);
    expect(denyCells.length).toBe(1);
  });

  it("renders zero numeric DOM (D120/D217 — no scores in the subset table)", () => {
    const { container } = render(<TaggedSubsetTable subset={baseSubset} />);
    expect(container.textContent ?? "").not.toMatch(/0?\.\d/);
  });
});
