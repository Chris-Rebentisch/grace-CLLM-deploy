"use client";

// Tiny local pubsub for telemetry. CP9 wires this to the backend ingest
// endpoint. CP7 only needs the local bus so EC-4 violation tests can
// observe the events.

export type TelemetryEvent = {
  type: string;
  payload?: Record<string, unknown>;
  emitted_at: string;
};

type Listener = (event: TelemetryEvent) => void;

const listeners = new Set<Listener>();
const recorded: TelemetryEvent[] = [];
const MAX_RECORDED = 500;

export function emitTelemetry(
  type: string,
  payload?: Record<string, unknown>,
): TelemetryEvent {
  const event: TelemetryEvent = {
    type,
    payload,
    emitted_at: new Date().toISOString(),
  };
  recorded.push(event);
  if (recorded.length > MAX_RECORDED) recorded.shift();
  for (const listener of listeners) {
    try {
      listener(event);
    } catch {
      // Listeners must not throw into the emitter.
    }
  }
  return event;
}

export function onTelemetry(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getRecentTelemetry(): ReadonlyArray<TelemetryEvent> {
  return [...recorded];
}

export function clearRecentTelemetry() {
  recorded.length = 0;
}
