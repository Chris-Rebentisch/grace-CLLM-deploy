import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CalibrationProgressBar } from "@/components/autonomy/CalibrationProgressBar";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

describe("CalibrationProgressBar", () => {
  it("renders the progress label from backend", () => {
    render(
      <CalibrationProgressBar
        progress={{
          total_decisions: 25,
          min_reviews_for_calibration: 50,
          progress_label: "25 of 50 reviews",
        }}
      />,
    );
    expect(screen.getByTestId("progress-label").textContent).toBe(
      "25 of 50 reviews",
    );
  });

  it("renders the gate label from copy", () => {
    render(
      <CalibrationProgressBar
        progress={{
          total_decisions: 0,
          min_reviews_for_calibration: 50,
          progress_label: "0 of 50 reviews",
        }}
      />,
    );
    expect(screen.getByText(AUTONOMY_COPY.progressGateLabel)).toBeInTheDocument();
  });

  it("renders a progressbar role element", () => {
    render(
      <CalibrationProgressBar
        progress={{
          total_decisions: 50,
          min_reviews_for_calibration: 50,
          progress_label: "50 of 50 reviews",
        }}
      />,
    );
    const bar = screen.getByRole("progressbar");
    expect(bar).toBeInTheDocument();
    expect(bar.getAttribute("aria-valuenow")).toBe("100");
  });

  it("caps percentage at 100 when total exceeds min", () => {
    render(
      <CalibrationProgressBar
        progress={{
          total_decisions: 200,
          min_reviews_for_calibration: 50,
          progress_label: "200 of 50 reviews",
        }}
      />,
    );
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("100");
  });

  it("honors a custom testId override", () => {
    render(
      <CalibrationProgressBar
        progress={{
          total_decisions: 0,
          min_reviews_for_calibration: 50,
          progress_label: "0 of 50 reviews",
        }}
        testId="custom-progress"
      />,
    );
    expect(screen.getByTestId("custom-progress")).toBeInTheDocument();
  });
});
