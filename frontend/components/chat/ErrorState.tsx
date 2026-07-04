"use client";

import { Button } from "@/components/ui/button";
import { getApiBaseUrl } from "@/lib/api/client";
import { BackendError, ClientError, NetworkError, TimeoutError } from "@/lib/api/errors";
import type { ChatError } from "@/lib/state/chat-store";

export type ErrorStateProps = {
  error: ChatError;
  onRetry?: () => void;
  onDismiss?: () => void;
};

function stageMessage(stage: string): string {
  switch (stage) {
    case "retrieve":
      return "Retrieval failed before the graph could contribute evidence.";
    case "synthesize":
      return "The response model didn't return a usable answer.";
    case "span_detect":
      return "The response came back but certainty annotation failed.";
    default:
      return "The server couldn't assemble a response.";
  }
}

export function ErrorState({ error, onRetry, onDismiss }: ErrorStateProps) {
  let title = "Something went wrong";
  let body = "Try again — your message is preserved.";

  if (error instanceof BackendError) {
    title = `Backend error (${error.status})`;
    body = stageMessage(error.stage);
  } else if (error instanceof ClientError) {
    title = `Request rejected (${error.status})`;
    body = error.message || body;
  } else if (error instanceof NetworkError) {
    title = "Network error";
    body = `We couldn't reach the backend. Confirm it is running at ${getApiBaseUrl()}.`;
  } else if (error instanceof TimeoutError) {
    title = "Request timed out";
    body = "The backend is taking longer than expected.";
  } else if ("message" in error) {
    body = error.message;
  }

  return (
    <div
      role="alert"
      className="mx-4 my-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
      data-error-stage={error instanceof BackendError ? error.stage : error.kind}
    >
      <p className="font-medium">{title}</p>
      <p className="mt-1 text-destructive/90">{body}</p>
      <div className="mt-2 flex gap-2">
        {onRetry ? (
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
        {onDismiss ? (
          <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        ) : null}
      </div>
    </div>
  );
}
