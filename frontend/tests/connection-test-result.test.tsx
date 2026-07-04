import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConnectionTestResult } from "@/components/settings/ConnectionTestResult";

describe("ConnectionTestResult", () => {
  it("renders nothing when result is null", () => {
    const { container } = render(<ConnectionTestResult result={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders all status fields without exposing numeric confidence", () => {
    render(
      <ConnectionTestResult
        result={{
          healthy: true,
          model_available: true,
          provider: "ollama",
          model: "qwen2.5:7b",
          test_response: "pong",
          response_time_ms: 42,
          error: "",
        }}
      />,
    );
    expect(screen.getByTestId("conn-healthy").textContent).toBe("true");
    expect(screen.getByTestId("conn-model-available").textContent).toBe("true");
    expect(screen.getByTestId("conn-provider").textContent).toBe("ollama");
    expect(screen.getByTestId("conn-model").textContent).toBe("qwen2.5:7b");
    expect(screen.getByTestId("conn-response").textContent).toBe("pong");
    // D120/D217: no numeric confidence/score numerals appear in the DOM.
    const dom = screen.getByTestId("connection-test-result").innerHTML;
    expect(/\bconfidence\b/i.test(dom)).toBe(false);
  });

  it("renders error text when an error is present", () => {
    render(
      <ConnectionTestResult
        result={{
          healthy: false,
          model_available: false,
          provider: "anthropic",
          model: "claude-haiku-4-5-20251001",
          test_response: "",
          response_time_ms: 0,
          error: "401 Unauthorized",
        }}
      />,
    );
    expect(screen.getByTestId("conn-error").textContent).toMatch(/401/);
  });
});
