import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcePanel } from "@/components/provenance/SourcePanel";
import type { AssistantChatMessage } from "@/lib/state/chat-store";

function buildMessage(
  overrides: Partial<AssistantChatMessage> = {},
): AssistantChatMessage {
  return {
    id: "m-1",
    role: "assistant",
    content: "Hi.",
    sent_at: new Date().toISOString(),
    claim_spans: [
      {
        text: "Hi",
        sentence_indices: [0],
        start_char: 0,
        end_char: 2,
        certainty_band: "medium",
        span_confidence: "medium",
        supporting_grace_ids: [
          "aaaaaaaa-1111-4000-8000-000000000001",
          "aaaaaaaa-2222-4000-8000-000000000002",
        ],
      },
    ],
    model: "qwen2.5:7b",
    provider: "ollama",
    strategy_contributions: { graph: 3, semantic: 2 },
    latency_ms: { total: 2300 },
    response_metadata: {
      context_truncated: false,
      span_detector_mode: "sentence_fallback",
      phase_style_applied: "none",
      span_detection_note: null,
      model_override_applied: false,
    },
    ...overrides,
  };
}

describe("SourcePanel", () => {
  it("toggles open on click and surfaces model/provider + evidence count", async () => {
    render(<SourcePanel message={buildMessage()} />);
    expect(screen.queryByTestId("source-panel-body")).toBeNull();

    await userEvent.click(screen.getByTestId("source-panel-toggle"));
    expect(screen.getByTestId("source-panel-body")).toBeInTheDocument();
    expect(screen.getByTestId("source-panel-model").textContent).toMatch(
      /qwen2\.5:7b/,
    );
    expect(screen.getByTestId("source-panel-model").textContent).toMatch(
      /ollama/,
    );
    expect(
      screen.getByTestId("source-panel-evidence-count").textContent,
    ).toMatch(/2 supporting references? across 1 span/);
  });

  it("does NOT render numeric confidence scores — only strategy counts and latency (ops data)", async () => {
    const { container } = render(<SourcePanel message={buildMessage()} />);
    await userEvent.click(screen.getByTestId("source-panel-toggle"));
    const text = container.textContent ?? "";
    // D120: no decimal confidence floats.
    expect(text).not.toMatch(/\b0\.\d+\b/);
    expect(text).not.toMatch(/confidence/i);
  });
});
