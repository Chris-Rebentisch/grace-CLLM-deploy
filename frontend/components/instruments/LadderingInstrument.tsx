"use client";
import { useState } from "react";
import { InstrumentShell } from "./InstrumentShell";
import { emitTelemetry } from "@/lib/telemetry/bus";

export type LadderingInstrumentProps = {
  parentId: string;
  onComplete?: () => void;
};

export function LadderingInstrument({ parentId, onComplete }: LadderingInstrumentProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [children, setChildren] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const startTime = useState(() => Date.now())[0];

  const addChild = () => {
    if (input.trim()) {
      setChildren((prev) => [...prev, input.trim()]);
      setInput("");
    }
  };

  const handleComplete = () => {
    emitTelemetry("laddering_step_completed", {
      step_index: stepIndex,
      parent_grace_id_hash: parentId,
      child_grace_id_hashes: children,
      step_duration_ms: Date.now() - startTime,
    });
    setStepIndex((prev) => prev + 1);
    onComplete?.();
  };

  return (
    <InstrumentShell instrumentName="Laddering" onComplete={handleComplete}>
      <div data-testid="laddering-instrument">
        <div className="mb-2 text-xs text-slate-600">Decompose: {parentId}</div>
        <div className="flex gap-2">
          <input type="text" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Add child concept..." className="flex-1 rounded border px-2 py-1 text-xs" data-testid="laddering-input" />
          <button type="button" onClick={addChild} className="rounded bg-slate-200 px-2 text-xs">Add</button>
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {children.map((c, i) => (
            <span key={i} className="rounded bg-slate-100 px-2 py-0.5 text-xs">{c}</span>
          ))}
        </div>
      </div>
    </InstrumentShell>
  );
}
