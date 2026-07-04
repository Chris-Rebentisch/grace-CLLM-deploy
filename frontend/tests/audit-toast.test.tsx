import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AuditToast } from "@/components/review/AuditToast";

describe("AuditToast", () => {
  it("renders audit-event ID after commit", () => {
    render(<AuditToast eventId="550e8400-e29b-41d4-a716-446655440000" visible={true} onDismiss={vi.fn()} />);
    expect(screen.getByTestId("audit-toast")).toBeTruthy();
    expect(screen.getByTestId("audit-event-id").textContent).toBe("550e8400-e29b-41d4-a716-446655440000");
  });
});
