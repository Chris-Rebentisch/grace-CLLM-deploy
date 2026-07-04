"use client";

export type ModeSelectionRationaleProps = {
  mode?: string;
  rationale?: string | null;
};

const DEFAULT_RATIONALE = "Structure mode selected for guided ontology decomposition with reviewer-led decision capture";

export function ModeSelectionRationale({ mode = "Structure", rationale }: ModeSelectionRationaleProps) {
  return (
    <div data-testid="mode-selection-rationale" className="mb-4 rounded-md border border-blue-200 bg-blue-50 p-3 text-xs text-blue-800">
      <div className="font-semibold">Mode: {mode}</div>
      <div className="mt-1">{rationale ?? DEFAULT_RATIONALE}</div>
    </div>
  );
}
