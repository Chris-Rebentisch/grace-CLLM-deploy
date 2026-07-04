import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  DEFAULT_LATENCY_MILESTONES,
  LatencyReassurance,
} from "@/components/chat/LatencyReassurance";

describe("LatencyReassurance", () => {
  it("renders nothing when inactive and progresses through milestones while active", async () => {
    const { rerender, queryByTestId, findByText } = render(
      <LatencyReassurance active={false} />,
    );
    expect(queryByTestId("latency-reassurance")).toBeNull();

    let currentMs = 0;
    rerender(
      <LatencyReassurance active now={() => currentMs} />,
    );
    await findByText(DEFAULT_LATENCY_MILESTONES[0].text);

    currentMs = 3_100;
    await screen.findByText(DEFAULT_LATENCY_MILESTONES[1].text);

    currentMs = 6_100;
    await screen.findByText(DEFAULT_LATENCY_MILESTONES[2].text);

    currentMs = 12_100;
    await screen.findByText(DEFAULT_LATENCY_MILESTONES[3].text);
  });

  it("clears the message when active flips back to false", async () => {
    let currentMs = 0;
    const { rerender, queryByTestId } = render(
      <LatencyReassurance active now={() => currentMs} />,
    );
    currentMs = 3_500;
    await screen.findByText(DEFAULT_LATENCY_MILESTONES[1].text);

    rerender(<LatencyReassurance active={false} now={() => currentMs} />);
    expect(queryByTestId("latency-reassurance")).toBeNull();
  });
});
