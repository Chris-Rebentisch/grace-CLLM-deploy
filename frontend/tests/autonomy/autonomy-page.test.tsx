import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import AutonomyPage from "@/app/autonomy/page";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";
import { clearRecentTelemetry, getRecentTelemetry } from "@/lib/telemetry/bus";
import type { CalibrationDashboardResponse } from "@/lib/api/types";

// Mock apiRequest
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
  getApiBaseUrl: () => "http://127.0.0.1:8000",
}));

const MOCK_DASHBOARD: CalibrationDashboardResponse = {
  tiers: [
    {
      tier: 1,
      bands: [
        { band_low: 0.0, band_high: 0.1, approval_rate: 0.9, sample_count: 10 },
        { band_low: 0.1, band_high: 0.2, approval_rate: 0.5, sample_count: 5 },
      ],
      trust_indicator: "high",
      progress: {
        total_decisions: 60,
        min_reviews_for_calibration: 50,
        progress_label: "60 of 50 reviews",
      },
      trust_score_state: {
        tier: 1,
        trust_score: 0.92,
        autonomy_threshold: 0.90,
        autonomy_enabled: false,
        window_size: 50,
        min_reviews_for_calibration: 50,
        risk_tolerance: 0.90,
        total_decisions: 60,
        regression_detected: false,
        last_computed_at: "2026-05-14T00:00:00Z",
      },
    },
    {
      tier: 2,
      bands: [],
      trust_indicator: "insufficient",
      progress: {
        total_decisions: 0,
        min_reviews_for_calibration: 50,
        progress_label: "0 of 50 reviews",
      },
      trust_score_state: {
        tier: 2,
        trust_score: 0,
        autonomy_threshold: 0.90,
        autonomy_enabled: false,
        window_size: 50,
        min_reviews_for_calibration: 50,
        risk_tolerance: 0.90,
        total_decisions: 0,
        regression_detected: false,
        last_computed_at: null,
      },
    },
    {
      tier: 3,
      bands: [],
      trust_indicator: "building",
      progress: {
        total_decisions: 20,
        min_reviews_for_calibration: 50,
        progress_label: "20 of 50 reviews",
      },
      trust_score_state: {
        tier: 3,
        trust_score: 0.75,
        autonomy_threshold: 0.90,
        autonomy_enabled: false,
        window_size: 50,
        min_reviews_for_calibration: 50,
        risk_tolerance: 0.90,
        total_decisions: 20,
        regression_detected: true,
        last_computed_at: "2026-05-13T00:00:00Z",
      },
    },
  ],
};

describe("AutonomyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearRecentTelemetry();
  });

  afterEach(() => {
    clearRecentTelemetry();
  });

  it("renders the page title", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_DASHBOARD,
    );

    render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("autonomy-page")).toBeInTheDocument();
    });
    expect(screen.getByText(AUTONOMY_COPY.pageTitle)).toBeInTheDocument();
  });

  it("renders three tier sections on successful load", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_DASHBOARD,
    );

    render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("tier-section-1")).toBeInTheDocument();
    });
    expect(screen.getByTestId("tier-section-2")).toBeInTheDocument();
    expect(screen.getByTestId("tier-section-3")).toBeInTheDocument();
  });

  it("emits calibration_dashboard_viewed telemetry on mount", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_DASHBOARD,
    );

    render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("tier-section-1")).toBeInTheDocument();
    });

    const events = getRecentTelemetry();
    const dashboardEvent = events.find(
      (e) => e.type === "calibration_dashboard_viewed",
    );
    expect(dashboardEvent).toBeDefined();
    expect(dashboardEvent?.payload?.tiers_loaded).toBe(3);
  });

  it("renders error state on API failure", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("Server error"),
    );

    render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("autonomy-page-error")).toBeInTheDocument();
    });
  });

  it("renders regression banner for tier 3", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_DASHBOARD,
    );

    render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("regression-banner-3")).toBeInTheDocument();
    });
    expect(
      screen.getByText(AUTONOMY_COPY.regressionBanner),
    ).toBeInTheDocument();
  });

  it("does not render regression banner for tier 1 (no regression)", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_DASHBOARD,
    );

    render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("tier-section-1")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("regression-banner-1"),
    ).not.toBeInTheDocument();
  });

  it("never renders raw trust_score floats in DOM (D120/D217)", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_DASHBOARD,
    );

    const { container } = render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByTestId("tier-section-1")).toBeInTheDocument();
    });

    const text = container.textContent ?? "";
    // None of the trust_score floats should appear in DOM text
    expect(text).not.toContain("0.92");
    expect(text).not.toContain("0.75");
  });

  it("renders tier labels from copy", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      MOCK_DASHBOARD,
    );

    render(<AutonomyPage />);
    await waitFor(() => {
      expect(screen.getByText(AUTONOMY_COPY.tierLabel1)).toBeInTheDocument();
    });
    expect(screen.getByText(AUTONOMY_COPY.tierLabel2)).toBeInTheDocument();
    expect(screen.getByText(AUTONOMY_COPY.tierLabel3)).toBeInTheDocument();
  });
});
