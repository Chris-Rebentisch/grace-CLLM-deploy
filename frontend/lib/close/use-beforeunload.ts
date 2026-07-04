"use client";

import { useEffect } from "react";

// D200/D203 `beforeunload` hook. Browsers customize the displayed copy
// (D203 lock), so we only register the listener. Returning a string sets
// the deprecated `returnValue` which most browsers still honor as a
// trigger signal.
export function useBeforeunload(shouldWarn: boolean, message?: string) {
  useEffect(() => {
    if (!shouldWarn) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      const copy =
        message ??
        "You have unsaved changes to your session summary. Leave anyway?";
      e.returnValue = copy; // deprecated but still required
      return copy;
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [shouldWarn, message]);
}
