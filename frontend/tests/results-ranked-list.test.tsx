import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ResultsRankedList } from "@/components/inspector/ResultsRankedList";
import type { RankedResult } from "@/lib/api/types";

const RESULTS: RankedResult[] = [
  {
    grace_id: "g-1",
    entity_type: "Legal_Entity",
    name: "Acme",
    properties: {},
    rerank_score: 0.92,
    rrf_score: 0.88,
    contributing_strategies: ["graph", "semantic"],
    hop_distance: null,
  },
  {
    grace_id: "g-2",
    entity_type: "Contract",
    name: "Lease",
    properties: {},
    rerank_score: 0.42,
    rrf_score: 0.37,
    contributing_strategies: ["bm25"],
    hop_distance: null,
  },
];

describe("ResultsRankedList", () => {
  it("renders a row per result with rank ordinals (allowed per D217)", () => {
    render(
      <ResultsRankedList
        results={RESULTS}
        selectedIndex={null}
      />,
    );
    expect(screen.getByTestId("result-rank-0").textContent).toBe("#1");
    expect(screen.getByTestId("result-rank-1").textContent).toBe("#2");
    expect(screen.getByTestId("result-row-0").textContent).toContain("Acme");
  });

  it("does NOT render rerank_score / rrf_score as numerals (D217)", () => {
    render(
      <ResultsRankedList results={RESULTS} selectedIndex={null} />,
    );
    const rows = screen.getByTestId("results-ranked-list").textContent ?? "";
    expect(rows).not.toMatch(/0\.92/);
    expect(rows).not.toMatch(/0\.88/);
    expect(rows).not.toMatch(/0\.42/);
    expect(rows).not.toMatch(/0\.37/);
    expect(rows).not.toMatch(/rerank_score/);
    expect(rows).not.toMatch(/rrf_score/);
    // Bar widths DO render (without numeric labels)
    expect(screen.getByTestId("result-rerank-bar-0")).toBeTruthy();
  });
});
