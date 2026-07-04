import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CustomPathField } from "@/components/sources/CustomPathField";

describe("CustomPathField", () => {
  it("invokes onScan with the typed path when Re-scan is clicked", () => {
    const onScan = vi.fn();
    render(<CustomPathField onScan={onScan} />);
    const input = screen.getByTestId("custom-path-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "/tmp/sources" } });
    fireEvent.click(screen.getByTestId("custom-path-scan"));
    expect(onScan).toHaveBeenCalledWith("/tmp/sources");
  });

  it("does not invoke onScan when the path is empty", () => {
    const onScan = vi.fn();
    render(<CustomPathField onScan={onScan} />);
    fireEvent.click(screen.getByTestId("custom-path-scan"));
    expect(onScan).not.toHaveBeenCalled();
  });
});
