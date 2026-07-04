import { apiClient } from "./client";
import type {
  CloseConfirmRequest,
  CloseConfirmResponse,
  CloseSummaryRequest,
  CloseSummaryResponse,
  RegenerationQuery,
  RegenerationResponse,
} from "./types";

// D189 transport abstraction. Blocking-fetch in v1; a future SSE transport
// implements the same interface so the swap is localized.
export interface ChatTransport {
  sendQuery(
    req: RegenerationQuery,
    signal?: AbortSignal,
  ): Promise<RegenerationResponse>;
  sendCloseSummary(
    req: CloseSummaryRequest,
    signal?: AbortSignal,
  ): Promise<CloseSummaryResponse>;
  sendCloseConfirm(
    req: CloseConfirmRequest,
    signal?: AbortSignal,
  ): Promise<CloseConfirmResponse>;
}

export class BlockingFetchTransport implements ChatTransport {
  sendQuery(req: RegenerationQuery, signal?: AbortSignal) {
    return apiClient.post<RegenerationResponse>(
      "/api/regeneration/query",
      req,
      { signal },
    );
  }

  sendCloseSummary(req: CloseSummaryRequest, signal?: AbortSignal) {
    return apiClient.post<CloseSummaryResponse>(
      "/api/regeneration/close-summary",
      req,
      { signal },
    );
  }

  sendCloseConfirm(req: CloseConfirmRequest, signal?: AbortSignal) {
    return apiClient.post<CloseConfirmResponse>(
      "/api/regeneration/close-confirm",
      req,
      { signal },
    );
  }
}

let singleton: ChatTransport | null = null;

export function getChatTransport(): ChatTransport {
  if (!singleton) singleton = new BlockingFetchTransport();
  return singleton;
}

export function setChatTransport(transport: ChatTransport | null) {
  singleton = transport;
}
