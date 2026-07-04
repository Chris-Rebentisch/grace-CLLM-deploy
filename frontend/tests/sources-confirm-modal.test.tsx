import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SourcesConfirmModal } from "@/components/sources/SourcesConfirmModal";

describe("SourcesConfirmModal", () => {
  it("renders nothing when preview is null", () => {
    const { container } = render(
      <SourcesConfirmModal preview={null} onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders preview rows and fires onConfirm/onCancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <SourcesConfirmModal
        preview={{
          manifest_path: "/abs/path/manifest.json",
          total_files: 42,
          by_extension: { ".pdf": 30, ".docx": 12 },
          estimated_processing_minutes: 7,
        }}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByTestId("confirm-file-count").textContent).toBe("42");
    expect(screen.getByTestId("confirm-processing-minutes").textContent).toMatch(/7/);
    fireEvent.click(screen.getByTestId("confirm-cancel"));
    expect(onCancel).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-accept"));
    expect(onConfirm).toHaveBeenCalled();
  });
});
