import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CQGenerationPanel } from "@/components/onboarding/CQGenerationPanel";

vi.mock("@/lib/api/client", () => ({
  apiClient: {
    generateCqs: vi.fn(),
    getGenerationStatus: vi.fn(),
    cancelGeneration: vi.fn(),
    mergeCqs: vi.fn(),
    getCqMergeStatus: vi.fn(),
  },
}));

import { apiClient } from "@/lib/api/client";
const mocked = apiClient as unknown as Record<string, ReturnType<typeof vi.fn>>;

describe("CQGenerationPanel auto-merge wiring", () => {
  beforeEach(() => vi.clearAllMocks());

  it("auto-runs the merge after generation and shows the canonical count", async () => {
    mocked.generateCqs.mockResolvedValue({ status: "started", run_id: "gen-1" });
    mocked.getGenerationStatus.mockResolvedValue({
      run_id: "gen-1",
      completed_at: "2026-06-08T00:00:00Z",
      total_cqs_generated: 206,
      cancelled: false,
    });
    mocked.mergeCqs.mockResolvedValue({ status: "started", run_id: "merge-1" });
    mocked.getCqMergeStatus.mockResolvedValue({
      run_id: "merge-1",
      status: "completed",
      canonical_count: 50,
      total_cqs_input: 206,
    });
    const onGenerated = vi.fn();

    render(<CQGenerationPanel docCount={25} cqCount={0} onGenerated={onGenerated} />);
    fireEvent.click(screen.getByTestId("generate-cqs"));

    await waitFor(() =>
      expect(screen.getByTestId("cq-canonical-ready")).toBeInTheDocument(),
    );
    expect(mocked.mergeCqs).toHaveBeenCalledTimes(1);
    expect(onGenerated).toHaveBeenCalledWith(206);
    const text = screen.getByTestId("cq-canonical-ready").textContent ?? "";
    expect(text).toContain("50");
    expect(text).toContain("206"); // "collapsed from 206 raw"
  });

  it("skips the merge when generation was stopped by the operator", async () => {
    mocked.generateCqs.mockResolvedValue({ status: "started", run_id: "gen-2" });
    mocked.getGenerationStatus.mockResolvedValue({
      run_id: "gen-2",
      completed_at: "2026-06-08T00:00:00Z",
      total_cqs_generated: 30,
      cancelled: true,
    });

    render(<CQGenerationPanel docCount={25} cqCount={0} onGenerated={vi.fn()} />);
    fireEvent.click(screen.getByTestId("generate-cqs"));

    await waitFor(() => expect(screen.getByTestId("cq-stopped")).toBeInTheDocument());
    expect(mocked.mergeCqs).not.toHaveBeenCalled();
  });

  it("treats a merge failure as non-fatal and keeps the generated questions", async () => {
    mocked.generateCqs.mockResolvedValue({ status: "started", run_id: "gen-3" });
    mocked.getGenerationStatus.mockResolvedValue({
      run_id: "gen-3",
      completed_at: "2026-06-08T00:00:00Z",
      total_cqs_generated: 206,
      cancelled: false,
    });
    mocked.mergeCqs.mockResolvedValue({ status: "started", run_id: "merge-3" });
    mocked.getCqMergeStatus.mockResolvedValue({
      run_id: "merge-3",
      status: "failed",
      error_message: "ollama down",
    });

    render(<CQGenerationPanel docCount={25} cqCount={0} onGenerated={vi.fn()} />);
    fireEvent.click(screen.getByTestId("generate-cqs"));

    await waitFor(() =>
      expect(screen.getByTestId("cq-merge-warning")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("cq-merge-warning").textContent).toContain(
      "auto-merge failed",
    );
  });
});
