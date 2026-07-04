import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FeedbackThumbs } from "@/components/chat/FeedbackThumbs";

describe("FeedbackThumbs (Chunk 35a, D266)", () => {
  it("submits an up vote immediately without exposing freetext", async () => {
    const onSubmit = vi.fn().mockResolvedValue({ feedback_id: "fid-1" });
    render(<FeedbackThumbs queryEventId="qe-1" onSubmit={onSubmit} />);

    fireEvent.click(screen.getByTestId("feedback-thumbs-up"));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    const arg = onSubmit.mock.calls[0]![0]!;
    expect(arg.query_event_id).toBe("qe-1");
    expect(arg.vote).toBe("up");
    expect("freetext" in arg).toBe(false);

    expect(screen.queryByTestId("feedback-thumbs-freetext")).toBeNull();
    await waitFor(() =>
      expect(
        screen.getByTestId("feedback-thumbs").getAttribute("data-status"),
      ).toBe("submitted"),
    );
  });

  it("down vote stages a freetext panel and submits the trimmed text", async () => {
    const onSubmit = vi.fn().mockResolvedValue({ feedback_id: "fid-2" });
    render(<FeedbackThumbs queryEventId="qe-2" onSubmit={onSubmit} />);

    fireEvent.click(screen.getByTestId("feedback-thumbs-down"));

    const ft = screen.getByTestId("feedback-thumbs-freetext") as HTMLTextAreaElement;
    fireEvent.change(ft, {
      target: { value: "  result was wrong fund  " },
    });
    fireEvent.click(screen.getByTestId("feedback-thumbs-submit"));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    expect(onSubmit.mock.calls[0]![0]).toEqual({
      query_event_id: "qe-2",
      vote: "down",
      freetext: "result was wrong fund",
    });
  });

  it("clamps freetext to the 2048-char ceiling on input", () => {
    const onSubmit = vi.fn().mockResolvedValue({});
    render(<FeedbackThumbs queryEventId="qe-3" onSubmit={onSubmit} />);

    fireEvent.click(screen.getByTestId("feedback-thumbs-down"));
    const ft = screen.getByTestId("feedback-thumbs-freetext") as HTMLTextAreaElement;
    fireEvent.change(ft, { target: { value: "x".repeat(3000) } });
    expect(ft.value.length).toBe(2048);
  });
});
