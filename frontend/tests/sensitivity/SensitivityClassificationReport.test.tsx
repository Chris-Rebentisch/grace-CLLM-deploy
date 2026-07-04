import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SensitivityClassificationReport } from "@/components/sensitivity/SensitivityClassificationReport";
import type { SensitivityClassificationReportResponse } from "@/lib/api/types";

function makeReport(
  overrides: Partial<SensitivityClassificationReportResponse> = {},
): SensitivityClassificationReportResponse {
  return {
    report_id: "rpt-1",
    permission_matrix_id: "m-1",
    generated_at: "2026-05-09T00:00:00Z",
    tag_inventory: [
      { tag_name: "pii", rule_count: 3, cluster_count: 2, framework_codes: [] },
    ],
    coverage_breakdown: [
      {
        resource_kind: "ontology_module",
        action: "view",
        total_rule_count: 5,
        tagged_rule_count: 3,
      },
    ],
    untagged_rules: [],
    truncated: false,
    coverage_band: "high",
    corpus_below_floor: false,
    tag_hygiene_findings: [],
    ...overrides,
  };
}

describe("SensitivityClassificationReport", () => {
  it("renders the report id and coverage badge for a populated report", () => {
    render(<SensitivityClassificationReport report={makeReport()} />);
    expect(screen.getByTestId("sensitivity-report-id").textContent).toBe(
      "rpt-1",
    );
    expect(
      screen.getByTestId("sensitivity-coverage-badge-high"),
    ).toBeInTheDocument();
  });

  it("renders the below-floor banner when corpus_below_floor=true", () => {
    render(
      <SensitivityClassificationReport
        report={makeReport({ corpus_below_floor: true, coverage_band: null })}
      />,
    );
    expect(
      screen.getByTestId("sensitivity-below-floor-banner"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("sensitivity-coverage-badge-below-floor"),
    ).toBeInTheDocument();
  });

  it("renders the truncation banner when truncated=true", () => {
    render(
      <SensitivityClassificationReport
        report={makeReport({
          truncated: true,
          untagged_rules: [
            {
              cluster_id: "c1",
              cluster_display_name: "Finance",
              resource_kind: "ontology_module",
              resource_label: "finance",
              action: "view",
            },
          ],
        })}
      />,
    );
    expect(
      screen.getByTestId("sensitivity-untagged-truncated"),
    ).toBeInTheDocument();
  });

  it("renders coverage breakdown rows", () => {
    render(<SensitivityClassificationReport report={makeReport()} />);
    expect(
      screen.getByTestId("sensitivity-coverage-cell-ontology_module-view"),
    ).toBeInTheDocument();
  });

  it("does not surface a numeric coverage_score (D120/D217)", () => {
    const { container } = render(
      <SensitivityClassificationReport report={makeReport()} />,
    );
    expect(container.textContent ?? "").not.toMatch(/coverage_score/i);
    expect(container.textContent ?? "").not.toMatch(/0?\.\d{2,}/);
  });
});
