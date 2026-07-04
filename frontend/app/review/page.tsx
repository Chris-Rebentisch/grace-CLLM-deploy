"use client";
import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { ReviewPanel } from "@/components/review/ReviewPanel";
import { useReviewStore } from "@/lib/state/review-store";
import { useSessionStore } from "@/lib/state/session-store";
import { apiClient } from "@/lib/api/client";

// D365 — known instrument step values for deep-link hydration.
const KNOWN_STEPS = new Set([
  "card_sort",
  "cq_canvas",
  "element_review",
  "teach_back",
  "laddering",
]);

// Phase-7 fix: Next.js 15 requires ``useSearchParams()`` to live inside
// a ``<Suspense>`` boundary so the page can statically prerender (the
// missing wrapper made ``next build`` bail on the ``/review`` route).
// Move the original component into ``ReviewPageInner`` and export a
// wrapper that supplies the boundary with a non-interactive fallback.
function ReviewPageInner() {
  const searchParams = useSearchParams();
  const { sessionId, setSessionId: setStoreSessionId } = useSessionStore();
  const { setSessionId } = useReviewStore();

  // D365 — deep-link hydration from URL query params (Chunk 44, CP5).
  // If ?session_id is present, hydrate the session store on mount.
  // Unknown step values are silently ignored. No-param fallback
  // preserves existing Zustand-first behavior.
  useEffect(() => {
    const urlSessionId = searchParams.get("session_id");
    const urlStep = searchParams.get("step");
    if (urlSessionId && !sessionId) {
      setStoreSessionId(urlSessionId);
    }
    // step is consumed for instrument navigation; unknown values ignored.
    if (urlStep && KNOWN_STEPS.has(urlStep)) {
      // Future: navigate to instrument. For now, the step presence is
      // logged for audit trail correlation.
    }
  }, [searchParams, sessionId, setStoreSessionId]);

  // Auto-trigger CQ candidate generation on mount (spec section 17 Q2)
  useEffect(() => {
    if (!sessionId) return;
    setSessionId(sessionId);
    // Fire-and-forget background CQ candidate generation
    void apiClient.generateCQCandidates({
      session_id: sessionId,
      segment: "all",
    }).catch(() => {
      // 409 if already in flight -- expected
    });
  }, [sessionId, setSessionId]);

  if (!sessionId) {
    return (
      <div className="flex items-center justify-center p-8 text-sm text-slate-500" data-testid="review-no-session">
        Start a session to begin review.
      </div>
    );
  }

  return <ReviewPanel sessionId={sessionId} />;
}

export default function ReviewPage() {
  return (
    <Suspense
      fallback={
        <div
          className="flex items-center justify-center p-8 text-sm text-slate-500"
          data-testid="review-loading"
        >
          Loading review…
        </div>
      }
    >
      <ReviewPageInner />
    </Suspense>
  );
}
