import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { FlagAsIntentionalChangeButton } from "@/components/review/FlagAsIntentionalChangeButton";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("FlagAsIntentionalChangeButton", () => {
  it("renders trigger and opens drawer with authoring form on click", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    render(
      <FlagAsIntentionalChangeButton
        sessionId="00000000-0000-0000-0000-000000000001"
        elementName="Legal_Entity"
      />,
    );
    const trigger = screen.getByTestId("flag-intentional-change");
    expect(trigger).toBeTruthy();
    fireEvent.click(trigger);
    expect(screen.getByTestId("cd-authoring-form")).toBeTruthy();
  });
});
