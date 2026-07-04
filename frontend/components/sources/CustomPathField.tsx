"use client";
import { useState } from "react";

export function CustomPathField({ onScan }: { onScan: (rootDir: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="flex items-center gap-2 text-xs" data-testid="custom-path-field">
      <input
        data-testid="custom-path-input"
        className="flex-1 rounded border px-2 py-1"
        placeholder="/absolute/path/to/scan"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button
        type="button"
        data-testid="custom-path-scan"
        onClick={() => value && onScan(value)}
        className="rounded bg-slate-800 px-2 py-1 text-white"
      >
        Re-scan
      </button>
    </div>
  );
}
