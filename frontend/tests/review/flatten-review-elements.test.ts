import { describe, expect, it } from "vitest";
import { flattenReviewElements } from "@/lib/query/review";

describe("flattenReviewElements", () => {
  it("flattens the {entity_types, relationships} dict into a typed array", () => {
    const raw = {
      entity_types: [{ name: "Legal_Entity", status: "pending", decision: null }],
      relationships: [{ name: "has_party", status: "pending", decision: null }],
    };
    const out = flattenReviewElements(raw);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ element_type: "entity_type", element_name: "Legal_Entity" });
    expect(out[1]).toMatchObject({ element_type: "relationship_type", element_name: "has_party" });
  });

  it("passes a flat array through unchanged", () => {
    const arr = [{ element_type: "entity_type", element_name: "X" }];
    expect(flattenReviewElements(arr)).toEqual(arr);
  });

  it("handles empty/missing keys without throwing", () => {
    expect(flattenReviewElements({})).toEqual([]);
    expect(flattenReviewElements(null)).toEqual([]);
  });
});
