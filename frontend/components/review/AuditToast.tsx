"use client";
import { useEffect, useState } from "react";

export type AuditToastProps = {
  eventId: string;
  visible: boolean;
  onDismiss: () => void;
};

export function AuditToast({ eventId, visible, onDismiss }: AuditToastProps) {
  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(onDismiss, 5000);
    return () => clearTimeout(timer);
  }, [visible, onDismiss]);

  if (!visible) return null;

  return (
    <div data-testid="audit-toast" className="fixed bottom-4 right-4 z-50 rounded-md bg-slate-800 px-4 py-2 text-xs text-white shadow-lg">
      <div>Decision recorded</div>
      <div className="mt-0.5 font-mono text-[10px] text-slate-300" data-testid="audit-event-id">{eventId}</div>
    </div>
  );
}
