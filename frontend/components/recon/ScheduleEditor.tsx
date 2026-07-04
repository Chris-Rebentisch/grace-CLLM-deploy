"use client";

// Documented Reality Report schedule editor (Chunk 37, D287 / D288).
//
// Drawer-style modal (Dialog) with cadence selector and a preview line.
// Submit invokes the schedule CRUD POST/PATCH endpoints.

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  SCHEDULE_EDITOR_CADENCE_LABELS,
  SCHEDULE_EDITOR_CANCEL,
  SCHEDULE_EDITOR_PREVIEW,
  SCHEDULE_EDITOR_SUBMIT,
  SCHEDULE_EDITOR_TITLE,
} from "@/lib/recon/report_copy";
import type {
  DocumentedRealityCadence,
  DocumentedRealityScheduleRequest,
} from "@/lib/api/recon-types";

export type ScheduleEditorProps = {
  open: boolean;
  initialCadence?: DocumentedRealityCadence;
  initialEnabled?: boolean;
  onSubmit: (req: DocumentedRealityScheduleRequest) => Promise<void> | void;
  onCancel: () => void;
};

const CADENCES: DocumentedRealityCadence[] = [
  "quarterly",
  "monthly",
  "on_demand",
];

export function ScheduleEditor({
  open,
  initialCadence = "monthly",
  initialEnabled = true,
  onSubmit,
  onCancel,
}: ScheduleEditorProps) {
  const [cadence, setCadence] = useState<DocumentedRealityCadence>(
    initialCadence,
  );
  const [enabled, setEnabled] = useState<boolean>(initialEnabled);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await onSubmit({ cadence, enabled });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent data-testid="schedule-editor">
        <DialogHeader>
          <DialogTitle>{SCHEDULE_EDITOR_TITLE}</DialogTitle>
          <DialogDescription>
            {SCHEDULE_EDITOR_PREVIEW(cadence)}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3" role="radiogroup">
          {CADENCES.map((c) => (
            <label
              key={c}
              className="flex items-center gap-2 text-sm"
              data-testid={`schedule-editor-cadence-${c}`}
            >
              <input
                type="radio"
                name="cadence"
                value={c}
                checked={cadence === c}
                onChange={() => setCadence(c)}
              />
              {SCHEDULE_EDITOR_CADENCE_LABELS[c]}
            </label>
          ))}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              data-testid="schedule-editor-enabled"
            />
            Enabled
          </label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={submitting}>
            {SCHEDULE_EDITOR_CANCEL}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting}
            data-testid="schedule-editor-submit"
          >
            {SCHEDULE_EDITOR_SUBMIT}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
