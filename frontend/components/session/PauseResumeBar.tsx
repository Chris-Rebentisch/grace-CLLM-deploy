"use client";

import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/lib/state/session-store";

export type PauseResumeBarProps = {
  onClose?: () => void;
};

// EC-5: no cooldown timer, no decay UI, no "progress lost" warning,
// no streak counter. The resume UI is equivalent to fresh UI except
// for the "Resuming from {phase}" affordance.
export function PauseResumeBar({ onClose }: PauseResumeBarProps) {
  const status = useSessionStore((s) => s.sessionStatus);
  const pauseSession = useSessionStore((s) => s.pauseSession);
  const resumeSession = useSessionStore((s) => s.resumeSession);
  const resumedFrom = useSessionStore((s) => s.resumedFrom);

  if (status === "idle" || status === "closed") return null;

  return (
    <div className="flex items-center gap-2" data-testid="pause-resume-bar">
      {status === "paused" ? (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => resumeSession()}
        >
          Resume{resumedFrom ? ` from ${resumedFrom}` : ""}
        </Button>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => pauseSession()}
        >
          Pause
        </Button>
      )}
      {onClose ? (
        <Button type="button" size="sm" variant="ghost" onClick={onClose}>
          Close session
        </Button>
      ) : null}
    </div>
  );
}
