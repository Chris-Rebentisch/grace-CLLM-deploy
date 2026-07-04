import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SerializedContextViewer } from "@/components/inspector/SerializedContextViewer";

// Includes a TemplateSerializer-style embedded confidence numeral. D217.3
// exempts this content from the numeric-score filter.
const SERIALIZED = `Entity: Acme (Legal_Entity, confidence=0.92)
  - signed Master Agreement (Contract)
  - based in Delaware`;

describe("SerializedContextViewer", () => {
  it("renders the serialized text verbatim", () => {
    render(
      <SerializedContextViewer serialized={SERIALIZED} format="template" />,
    );
    const pre = screen.getByTestId("serialized-context-verbatim");
    expect(pre.textContent).toBe(SERIALIZED);
    expect(screen.getByTestId("serialization-format").textContent).toBe(
      "format: template",
    );
  });

  it("sets data-serialized-context-verbatim=\"true\" on the text panel (D217.3 exemption anchor)", () => {
    render(
      <SerializedContextViewer serialized={SERIALIZED} format="template" />,
    );
    const pre = screen.getByTestId("serialized-context-verbatim");
    expect(pre.getAttribute("data-serialized-context-verbatim")).toBe("true");
  });
});
