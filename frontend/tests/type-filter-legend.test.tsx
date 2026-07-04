import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TypeFilterLegend } from "@/components/graph/TypeFilterLegend";
import { useGraphStore } from "@/lib/state/graph-store";

beforeEach(() => {
  useGraphStore.getState().reset();
});

describe("TypeFilterLegend", () => {
  it("renders entity and relationship type sections with per-type counts", () => {
    render(
      <TypeFilterLegend
        entityTypes={[
          { type: "Legal_Entity", count: 12, module: "legal_entity" },
          { type: "Contract", count: 5, module: "contract" },
        ]}
        relationshipTypes={[
          { type: "owns", count: 9, module: null },
        ]}
      />,
    );

    expect(
      screen.getByTestId("entity-count-Legal_Entity").textContent,
    ).toBe("12");
    expect(screen.getByTestId("entity-count-Contract").textContent).toBe("5");
    expect(screen.getByTestId("rel-count-owns").textContent).toBe("9");
  });

  it("checkbox toggles visibleEntityTypes in the store", () => {
    render(
      <TypeFilterLegend
        entityTypes={[
          { type: "Legal_Entity", count: 1, module: null },
        ]}
        relationshipTypes={[]}
      />,
    );
    const cb = screen.getByTestId(
      "entity-toggle-Legal_Entity",
    ) as HTMLInputElement;
    expect(cb.checked).toBe(false);
    fireEvent.click(cb);
    expect(
      useGraphStore.getState().visibleEntityTypes.has("Legal_Entity"),
    ).toBe(true);
  });
});
