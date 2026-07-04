// Error classes normalized from backend responses. Stage mapping follows
// Chunk 23 D135: 503 = retrieve, 500 = assemble, 502 = synthesize.

export type BackendStage = "retrieve" | "assemble" | "synthesize" | "span_detect";

export class TimeoutError extends Error {
  readonly kind = "timeout" as const;
  constructor(message = "Request timed out") {
    super(message);
    this.name = "TimeoutError";
  }
}

export class NetworkError extends Error {
  readonly kind = "network" as const;
  constructor(message = "Network error", public readonly cause?: unknown) {
    super(message);
    this.name = "NetworkError";
  }
}

export class ClientError extends Error {
  readonly kind = "client" as const;
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "ClientError";
  }
}

export class BackendError extends Error {
  readonly kind = "backend" as const;
  constructor(
    public readonly status: number,
    public readonly stage: BackendStage,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "BackendError";
  }
}

export function mapStatusToStage(status: number): BackendStage {
  if (status === 503) return "retrieve";
  if (status === 502) return "synthesize";
  // 500 and anything else defaults to assemble (D135 fallthrough).
  return "assemble";
}
