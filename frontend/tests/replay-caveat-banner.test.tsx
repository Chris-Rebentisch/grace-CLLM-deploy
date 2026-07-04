import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReplayCaveatBanner } from "@/components/inspector/ReplayCaveatBanner";
import { useInspectorStore } from "@/lib/state/inspector-store";

beforeEach(() => {
  useInspectorStore.getState().clearInspector();
});

describe("ReplayCaveatBanner (D211)", () => {
  it("renders on source=replay_button and source=chat_link, never on direct_nav", () => {
    // direct_nav → hidden
    useInspectorStore.getState().setSource("direct_nav");
    const { rerender } = render(<ReplayCaveatBanner />);
    expect(screen.queryByTestId("replay-caveat-banner")).toBeNull();

    // replay_button → visible
    useInspectorStore.getState().setSource("replay_button");
    rerender(<ReplayCaveatBanner />);
    expect(screen.getByTestId("replay-caveat-banner")).toBeTruthy();
    expect(
      screen.getByTestId("replay-caveat-banner").textContent,
    ).toMatch(/replay of the retrieval pipeline/i);

    // chat_link → visible
    useInspectorStore.getState().setSource("chat_link");
    rerender(<ReplayCaveatBanner />);
    expect(screen.getByTestId("replay-caveat-banner")).toBeTruthy();

    // null → hidden
    useInspectorStore.getState().setSource(null);
    rerender(<ReplayCaveatBanner />);
    expect(screen.queryByTestId("replay-caveat-banner")).toBeNull();
  });
});
