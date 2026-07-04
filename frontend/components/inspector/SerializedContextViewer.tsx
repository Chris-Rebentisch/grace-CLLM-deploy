"use client";

// D217.3 — serialized_context rendered verbatim. The root element carries
// `data-serialized-context-verbatim="true"` so `check-no-numeric-scores.sh`
// skips the subtree. Any confidence numerals embedded in the serializer's
// own output (TemplateSerializer's `confidence=0.92`-style) are preserved
// as part of the audit trail.

export type SerializedContextViewerProps = {
  serialized: string;
  format: string;
};

export function SerializedContextViewer({
  serialized,
  format,
}: SerializedContextViewerProps) {
  return (
    <div
      data-testid="serialized-context-viewer"
      className="bg-white border rounded-md"
    >
      <header className="px-3 py-2 border-b flex items-center justify-between bg-slate-50">
        <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
          Serialized context
        </h3>
        <span
          className="text-xs text-slate-500 font-mono"
          data-testid="serialization-format"
        >
          format: {format}
        </span>
      </header>
      <pre
        data-serialized-context-verbatim="true"
        data-testid="serialized-context-verbatim"
        className="text-xs font-mono p-3 whitespace-pre-wrap break-words max-h-96 overflow-y-auto text-slate-800"
      >
        {serialized}
      </pre>
    </div>
  );
}
