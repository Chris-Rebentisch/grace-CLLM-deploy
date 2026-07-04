"use client";

import { INGESTION_COPY } from "@/lib/ingestion/copy";

const SOURCE_TYPES = [
  { value: "mbox", label: INGESTION_COPY.sourceTypeMbox, deferred: false },
  { value: "eml", label: INGESTION_COPY.sourceTypeEml, deferred: false },
  { value: "msg", label: INGESTION_COPY.sourceTypeMsg, deferred: false },
  { value: "pst", label: INGESTION_COPY.sourceTypePst, deferred: false },
  { value: "imap", label: INGESTION_COPY.sourceTypeImap, deferred: false },
  { value: "exchange", label: INGESTION_COPY.sourceTypeExchange, deferred: false },
  { value: "gmail", label: INGESTION_COPY.sourceTypeGmail, deferred: false },
] as const;

interface SourceTypeSelectorProps {
  value: string | null;
  onChange: (sourceType: string) => void;
}

export function SourceTypeSelector({ value, onChange }: SourceTypeSelectorProps) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">{INGESTION_COPY.sourceTypeHeading}</h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {SOURCE_TYPES.map((st) => (
          <button
            key={st.value}
            onClick={() => !st.deferred && onChange(st.value)}
            disabled={st.deferred}
            className={`relative rounded-md border px-4 py-3 text-sm ${
              value === st.value
                ? "border-blue-600 bg-blue-50 text-blue-700"
                : st.deferred
                  ? "cursor-not-allowed border-gray-200 bg-gray-50 text-gray-400"
                  : "border-gray-300 hover:bg-gray-50"
            }`}
          >
            {st.label}
            {st.deferred && (
              <span className="mt-1 block text-xs text-amber-600">
                {INGESTION_COPY.deferredBadge}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
