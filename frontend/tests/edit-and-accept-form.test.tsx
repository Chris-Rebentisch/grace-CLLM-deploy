import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EditAndAcceptForm } from "@/components/claims/EditAndAcceptForm";
import type { ClaimRecord } from "@/lib/api/types";

function sampleClaim(): ClaimRecord {
  return {
    claim_id: "claim-1",
    extraction_event_id: "event-1",
    entity_type: "Legal_Entity",
    relationship_type: null,
    subject_name: "Acme Corp",
    predicate: "is a",
    object_name: "Legal_Entity",
    evidence_spans: [{ text: "Acme", start_char: 0, end_char: 4 }],
    status: "quarantined",
    verdict: "refuted",
    decision_source: "verifier",
    human_decided_at: null,
    ontology_module: "core",
    source_document_id: "doc-1",
    constraint_violations: null,
    verifier_contradiction_reason: null,
    supersedes_claim_id: null,
    created_at: "2026-05-01T00:00:00Z",
  };
}

describe("EditAndAcceptForm", () => {
  it("submits modifications when fields are edited", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <EditAndAcceptForm
        claim={sampleClaim()}
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );
    const subject = screen.getByTestId("edit-subject") as HTMLInputElement;
    fireEvent.change(subject, { target: { value: "Acme Corporation" } });
    fireEvent.click(screen.getByTestId("edit-submit"));
    expect(onSubmit).toHaveBeenCalledWith({
      subject_name: "Acme Corporation",
      predicate: "is a",
      object_name: "Legal_Entity",
      properties_json: null,
    });
  });

  it("rejects malformed JSON in properties textarea and shows error", () => {
    const onSubmit = vi.fn();
    render(
      <EditAndAcceptForm
        claim={sampleClaim()}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    const json = screen.getByTestId("edit-properties-json") as HTMLTextAreaElement;
    fireEvent.change(json, { target: { value: "{not valid" } });
    fireEvent.click(screen.getByTestId("edit-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("edit-error").textContent).toMatch(/valid JSON/);
  });

  it("calls onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    render(
      <EditAndAcceptForm
        claim={sampleClaim()}
        onSubmit={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("edit-cancel"));
    expect(onCancel).toHaveBeenCalled();
  });
});
