import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RiskToleranceConfig } from "@/components/autonomy/RiskToleranceConfig";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";
import type { TrustScoreState } from "@/lib/api/types";

// Mock apiRequest
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

const defaultTrustState: TrustScoreState = {
  tier: 1,
  trust_score: 0.85,
  autonomy_threshold: 0.90,
  autonomy_enabled: false,
  window_size: 50,
  min_reviews_for_calibration: 50,
  risk_tolerance: 0.90,
  total_decisions: 30,
  regression_detected: false,
  last_computed_at: null,
};

describe("RiskToleranceConfig", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders all three config dropdowns", () => {
    render(
      <RiskToleranceConfig tier={1} trustState={defaultTrustState} />,
    );
    expect(screen.getByTestId("risk-tolerance-select")).toBeInTheDocument();
    expect(screen.getByTestId("window-size-select")).toBeInTheDocument();
    expect(screen.getByTestId("min-reviews-select")).toBeInTheDocument();
  });

  it("renders the heading from copy", () => {
    render(
      <RiskToleranceConfig tier={1} trustState={defaultTrustState} />,
    );
    expect(
      screen.getByText(AUTONOMY_COPY.riskToleranceHeading),
    ).toBeInTheDocument();
  });

  it("save button is disabled when no changes", () => {
    render(
      <RiskToleranceConfig tier={1} trustState={defaultTrustState} />,
    );
    const btn = screen.getByTestId("save-config-button");
    expect(btn).toBeDisabled();
  });

  it("save button enables when a value changes", () => {
    render(
      <RiskToleranceConfig tier={1} trustState={defaultTrustState} />,
    );
    fireEvent.change(screen.getByTestId("risk-tolerance-select"), {
      target: { value: "0.95" },
    });
    expect(screen.getByTestId("save-config-button")).not.toBeDisabled();
  });

  it("calls onUpdated after successful save", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    const mockResponse: TrustScoreState = {
      ...defaultTrustState,
      risk_tolerance: 0.95,
    };
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(mockResponse);

    const onUpdated = vi.fn();
    render(
      <RiskToleranceConfig
        tier={1}
        trustState={defaultTrustState}
        onUpdated={onUpdated}
      />,
    );

    fireEvent.change(screen.getByTestId("risk-tolerance-select"), {
      target: { value: "0.95" },
    });
    fireEvent.click(screen.getByTestId("save-config-button"));

    await waitFor(() => {
      expect(onUpdated).toHaveBeenCalledWith(mockResponse);
    });
    expect(
      screen.getByTestId("config-success-msg"),
    ).toBeInTheDocument();
  });

  it("shows error on save failure", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("Network error"),
    );

    render(
      <RiskToleranceConfig tier={1} trustState={defaultTrustState} />,
    );

    fireEvent.change(screen.getByTestId("window-size-select"), {
      target: { value: "100" },
    });
    fireEvent.click(screen.getByTestId("save-config-button"));

    await waitFor(() => {
      expect(screen.getByTestId("config-error-msg")).toBeInTheDocument();
    });
  });

  it("never renders numeric trust_score in DOM", () => {
    const { container } = render(
      <RiskToleranceConfig tier={1} trustState={defaultTrustState} />,
    );
    const text = container.textContent ?? "";
    // D120/D217: trust_score must not surface as a decimal
    expect(text).not.toContain("0.85");
  });
});
