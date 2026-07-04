"use client";

export function VerifierNotePanel({ note }: { note: string | null }) {
  if (!note) return null;
  return (
    <details
      className="rounded border border-border bg-white p-2 text-xs text-slate-700"
      data-testid="verifier-note-panel"
    >
      <summary className="cursor-pointer font-medium">Verifier&apos;s note</summary>
      <p className="mt-2 whitespace-pre-wrap">{note}</p>
    </details>
  );
}
