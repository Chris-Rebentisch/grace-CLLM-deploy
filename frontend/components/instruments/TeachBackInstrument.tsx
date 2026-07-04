"use client";
import { useState } from "react";
import { InstrumentShell } from "./InstrumentShell";
import { emitTelemetry } from "@/lib/telemetry/bus";

export type TeachBackItem = {
  index: number;
  sentence: string;
};

export type TeachBackLabel = "correct" | "wrong" | "missing-something";

export type TeachBackInstrumentProps = {
  items: TeachBackItem[];
  onComplete?: () => void;
};

export function TeachBackInstrument({ items, onComplete }: TeachBackInstrumentProps) {
  const [labels, setLabels] = useState<Record<number, TeachBackLabel>>({});
  const [corrections, setCorrections] = useState<Record<number, string>>({});

  const setLabel = (index: number, label: TeachBackLabel) => {
    setLabels((prev) => ({ ...prev, [index]: label }));
  };

  const setCorrection = (index: number, text: string) => {
    setCorrections((prev) => ({ ...prev, [index]: text.slice(0, 240) }));
  };

  const handleComplete = () => {
    const correct = Object.values(labels).filter((l) => l === "correct").length;
    const wrong = Object.values(labels).filter((l) => l === "wrong").length;
    const missing = Object.values(labels).filter((l) => l === "missing-something").length;
    const correctionChars = Object.values(corrections).reduce((sum, c) => sum + c.length, 0);

    emitTelemetry("teach_back_completed", {
      item_index: 0,
      sentence_count: items.length,
      correct_count: correct,
      wrong_count: wrong,
      missing_something_count: missing,
      correction_chars_total: correctionChars,
    });
    onComplete?.();
  };

  return (
    <InstrumentShell instrumentName="Teach-Back">
      <div data-testid="teach-back-instrument">
        {items.map((item) => (
          <div key={item.index} className="mb-3 rounded border border-border p-2">
            <div className="mb-1 text-xs text-slate-700">{item.sentence}</div>
            <div role="radiogroup" aria-label={`Label for sentence ${item.index}`} data-testid="teach-back-radiogroup" className="flex gap-3">
              {(["correct", "wrong", "missing-something"] as TeachBackLabel[]).map((label) => (
                <label key={label} className="flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    name={`teach-back-${item.index}`}
                    value={label}
                    checked={labels[item.index] === label}
                    onChange={() => setLabel(item.index, label)}
                  />
                  {label}
                </label>
              ))}
            </div>
            {(labels[item.index] === "wrong" || labels[item.index] === "missing-something") && (
              <textarea
                value={corrections[item.index] ?? ""}
                onChange={(e) => setCorrection(item.index, e.target.value)}
                maxLength={240}
                placeholder="Correction (max 240 chars)..."
                className="mt-1 w-full rounded border px-2 py-1 text-xs"
                data-testid={`teach-back-textarea-${item.index}`}
              />
            )}
          </div>
        ))}
        <button type="button" onClick={handleComplete} data-testid="teach-back-complete" className="rounded bg-slate-800 px-3 py-1 text-xs text-white">
          Complete
        </button>
      </div>
    </InstrumentShell>
  );
}
