import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import CuratePage from "@/app/ingestion/setup/curate/page";
import { INGESTION_COPY } from "@/lib/ingestion/copy";
import { computeDiversityPreview } from "@/lib/ingestion/diversity-preview";
import { getSampleSizeGuidance } from "@/lib/ingestion/sample-size";

const originalFetch = globalThis.fetch;
const SOURCE_ID = "00000000-0000-0000-0000-000000000001";

function mockEvents(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    event_id: `ev-${i}`,
    message_id: `<msg-${i}@example.com>`,
    sender_email: `user${i % 3}@example.com`,
    sender_display_name: null,
    subject: `Subject ${i}`,
    sent_at: "2024-01-15T12:00:00Z",
    received_at: null,
    triage_tier_outcome: "pending",
  }));
}

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("CuratePage", () => {
  it("renders page title and description", () => {
    render(<CuratePage />);
    expect(screen.getByText(INGESTION_COPY.curatePageTitle)).toBeInTheDocument();
    expect(
      screen.getByText(INGESTION_COPY.curatePageDescription),
    ).toBeInTheDocument();
  });

  it("renders email list after load events", async () => {
    const events = mockEvents(2);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));

    await waitFor(() => {
      expect(screen.getByText("Subject 0")).toBeInTheDocument();
    });
    expect(screen.getByText(INGESTION_COPY.emailListHeading)).toBeInTheDocument();
  });

  it("toggles checkbox selection", async () => {
    const events = mockEvents(1);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));

    await waitFor(() => {
      expect(screen.getByRole("checkbox")).toBeInTheDocument();
    });

    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
  });

  it("shows three diversity bands in pre-submit preview when selected", async () => {
    const events = mockEvents(1);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));

    await waitFor(() => screen.getAllByRole("checkbox").length > 0);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);

    const preview = await screen.findByTestId("diversity-preview");
    expect(preview).toBeInTheDocument();
    const bands = computeDiversityPreview(events);
    expect(screen.getByText(bands.sender_band)).toBeInTheDocument();
    expect(screen.getByText(bands.thread_depth_band)).toBeInTheDocument();
    expect(screen.getByText(bands.date_range_band)).toBeInTheDocument();
  });

  it("renders band labels without numeric confidence tokens (D120/D217)", async () => {
    const events = mockEvents(1);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));
    await waitFor(() => screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("checkbox"));

    const preview = await screen.findByTestId("diversity-preview");
    expect(preview.textContent).not.toMatch(/0\.\d+/);
    expect(preview.textContent).not.toMatch(/confidence/i);
  });

  it("shows sample-size warning when fewer than 200 selected", async () => {
    const events = mockEvents(1);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));
    await waitFor(() => screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("checkbox"));

    expect(getSampleSizeGuidance(1)).toBe("warning_low");
    expect(
      screen.getByText(INGESTION_COPY.sampleSizeWarningLow),
    ).toBeInTheDocument();
  });

  it("shows representative sample message at 500+ selection count", () => {
    expect(getSampleSizeGuidance(500)).toBe("representative");
    render(
      <div data-testid="advisory-harness">
        {getSampleSizeGuidance(500) === "representative" &&
          INGESTION_COPY.sampleSizeRepresentative}
      </div>,
    );
    expect(screen.getByTestId("advisory-harness").textContent).toContain(
      INGESTION_COPY.sampleSizeRepresentative,
    );
  });

  it("calls POST /api/ingestion/curate on confirm", async () => {
    const events = mockEvents(1);
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: events }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            subset_id: "00000000-0000-0000-0000-000000000002",
            message_count: 1,
            diversity_metrics: {
              sender_band: "narrow",
              thread_depth_band: "mostly_single",
              date_range_band: "short",
            },
          }),
          { status: 201 },
        ),
      );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));
    await waitFor(() => screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText(INGESTION_COPY.curateButton));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    });
    const curateCall = vi
      .mocked(globalThis.fetch)
      .mock.calls.find((c) => c[0] === "/api/ingestion/curate");
    expect(curateCall).toBeDefined();
    expect(curateCall?.[1]?.method).toBe("POST");
  });

  it("shows thread depth v1 degradation notice in preview", async () => {
    const events = mockEvents(1);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));
    await waitFor(() => screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("checkbox"));

    expect(
      screen.getByText(INGESTION_COPY.threadDepthV1Notice),
    ).toBeInTheDocument();
  });

  it("email list uses scroll container for long lists", async () => {
    const events = mockEvents(5);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));

    await waitFor(() => {
      expect(screen.getByTestId("curation-email-scroll")).toBeInTheDocument();
    });
    const scroll = screen.getByTestId("curation-email-scroll");
    expect(scroll.className).toContain("overflow-y-auto");
    expect(scroll.className).toContain("max-h-96");
  });

  it("disables confirm CTA when nothing is selected", async () => {
    const events = mockEvents(1);
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: events }), { status: 200 }),
    );

    render(<CuratePage />);
    fireEvent.change(screen.getByPlaceholderText("UUID"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.click(screen.getByText(INGESTION_COPY.loadEventsButton));
    await waitFor(() => screen.getByText(INGESTION_COPY.curateButton));

    const button = screen.getByText(INGESTION_COPY.curateButton);
    expect(button).toBeDisabled();
  });
});
