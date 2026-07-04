import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";
import { PathSelector } from "@/components/ingestion/PathSelector";
import { SourceTypeSelector } from "@/components/ingestion/SourceTypeSelector";
import { SourceConfigForm } from "@/components/ingestion/SourceConfigForm";
import { TestConnectionButton } from "@/components/ingestion/TestConnectionButton";
import { ReadinessGate } from "@/components/ingestion/ReadinessGate";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn(async () =>
    new Response(JSON.stringify({}), { status: 200 }),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

// ---------- PathSelector ----------

describe("PathSelector", () => {
  it("renders A, B, and C path buttons", () => {
    render(<PathSelector value={null} onChange={() => {}} />);
    expect(screen.getByText(INGESTION_COPY.pathA)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.pathB)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.pathC)).toBeInTheDocument();
  });

  it("calls onChange when a path is selected", () => {
    const onChange = vi.fn();
    render(<PathSelector value={null} onChange={onChange} />);
    fireEvent.click(screen.getByText(INGESTION_COPY.pathA));
    expect(onChange).toHaveBeenCalledWith("A");
  });
});

// ---------- SourceTypeSelector ----------

describe("SourceTypeSelector", () => {
  it("renders all seven source type buttons", () => {
    render(<SourceTypeSelector value={null} onChange={() => {}} />);
    expect(screen.getByText(INGESTION_COPY.sourceTypeMbox)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.sourceTypeEml)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.sourceTypeMsg)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.sourceTypePst)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.sourceTypeImap)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.sourceTypeExchange)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.sourceTypeGmail)).toBeInTheDocument();
  });

  it("live network types do not show deferred badge", () => {
    render(<SourceTypeSelector value={null} onChange={() => {}} />);
    expect(screen.queryByText(INGESTION_COPY.deferredBadge)).not.toBeInTheDocument();
  });

  it("live network types call onChange when selected", () => {
    const onChange = vi.fn();
    render(<SourceTypeSelector value={null} onChange={onChange} />);
    fireEvent.click(screen.getByText(INGESTION_COPY.sourceTypeImap));
    expect(onChange).toHaveBeenCalledWith("imap");
  });

  it("file-based types call onChange", () => {
    const onChange = vi.fn();
    render(<SourceTypeSelector value={null} onChange={onChange} />);
    fireEvent.click(screen.getByText(INGESTION_COPY.sourceTypeMbox));
    expect(onChange).toHaveBeenCalledWith("mbox");
  });
});

// ---------- SourceConfigForm ----------

describe("SourceConfigForm", () => {
  it("renders file path field for mbox", () => {
    render(<SourceConfigForm sourceType="mbox" onSubmit={() => {}} />);
    expect(screen.getByText(INGESTION_COPY.filePathLabel)).toBeInTheDocument();
    expect(screen.getByText(INGESTION_COPY.segmentLabel)).toBeInTheDocument();
  });

  it("renders directory path field for eml", () => {
    render(<SourceConfigForm sourceType="eml" onSubmit={() => {}} />);
    expect(screen.getByText(INGESTION_COPY.directoryPathLabel)).toBeInTheDocument();
  });
});

// ---------- TestConnectionButton ----------

describe("TestConnectionButton", () => {
  it("renders the test connection button", () => {
    render(<TestConnectionButton sourceId="src-1" />);
    expect(
      screen.getByText(INGESTION_COPY.testConnectionButton),
    ).toBeInTheDocument();
  });

  it("shows success on ok response", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          ok: true,
          sample_message_count: 42,
          sample_date_range: null,
          error: null,
        }),
        { status: 200 },
      ),
    ) as unknown as typeof fetch;

    render(<TestConnectionButton sourceId="src-1" />);
    fireEvent.click(screen.getByText(INGESTION_COPY.testConnectionButton));

    await waitFor(() => {
      expect(
        screen.getByText(INGESTION_COPY.testConnectionSuccess),
      ).toBeInTheDocument();
    });
  });
});

// ---------- ReadinessGate ----------

describe("ReadinessGate", () => {
  it("renders loading state initially", () => {
    render(<ReadinessGate />);
    expect(screen.getByText("Loading readiness...")).toBeInTheDocument();
  });

  it("renders ready state when overall_ready is true", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          deployment_path: "A",
          segments: [],
          overall_ready: true,
          bootstrap_pending: false,
          thresholds: { cq_mention_threshold: 3, confidence_threshold: 0.85 },
        }),
        { status: 200 },
      ),
    ) as unknown as typeof fetch;

    render(<ReadinessGate />);

    await waitFor(() => {
      expect(
        screen.getByText(INGESTION_COPY.readinessReady),
      ).toBeInTheDocument();
    });
  });

  it("renders bootstrap pending when flagged", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          deployment_path: "B",
          segments: [],
          overall_ready: false,
          bootstrap_pending: true,
          thresholds: { cq_mention_threshold: 3, confidence_threshold: 0.85 },
        }),
        { status: 200 },
      ),
    ) as unknown as typeof fetch;

    render(<ReadinessGate />);

    await waitFor(() => {
      expect(
        screen.getByText(INGESTION_COPY.readinessBootstrapPending),
      ).toBeInTheDocument();
    });
  });

  it("renders per-segment breakdown", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          deployment_path: "A",
          segments: [
            {
              segment: "finance",
              ready: true,
              person_count: 12,
              organization_count: 5,
              accepted_cq_count: 4,
              guidance: "",
            },
          ],
          overall_ready: true,
          bootstrap_pending: false,
          thresholds: { cq_mention_threshold: 3, confidence_threshold: 0.85 },
        }),
        { status: 200 },
      ),
    ) as unknown as typeof fetch;

    render(<ReadinessGate />);

    await waitFor(() => {
      expect(screen.getByText("finance")).toBeInTheDocument();
    });
  });
});
