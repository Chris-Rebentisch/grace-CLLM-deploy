import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DecisionConfirmDialog } from "@/components/review/DecisionConfirmDialog";

describe("DecisionConfirmDialog", () => {
  it("typed confirmation for destructive decisions (reject/merge/split/reclassify)", () => {
    render(<DecisionConfirmDialog decision="rejected" elementName="Legal_Entity" payload={{ action: "reject" }} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByTestId("typed-confirmation")).toBeTruthy();
    expect(screen.getByTestId("decision-payload-preview")).toBeTruthy();
    // Confirm button disabled until typed
    expect((screen.getByTestId("confirm-destructive-btn") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("typed-confirm-input"), { target: { value: "LEGAL_ENTITY" } });
    expect((screen.getByTestId("confirm-destructive-btn") as HTMLButtonElement).disabled).toBe(false);
  });

  it("click-and-confirm for non-destructive decisions", () => {
    render(<DecisionConfirmDialog decision="approved" elementName="Company" payload={{ action: "approve" }} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByTestId("click-confirmation")).toBeTruthy();
    expect(screen.getByTestId("confirm-btn")).toBeTruthy();
  });
});
