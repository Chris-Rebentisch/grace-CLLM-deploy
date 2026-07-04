"use client";

import { useState } from "react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface PathSelectorProps {
  value: string | null;
  onChange: (path: string) => void;
}

const PATHS = [
  { value: "A", label: INGESTION_COPY.pathA },
  { value: "B", label: INGESTION_COPY.pathB },
  { value: "C", label: INGESTION_COPY.pathC },
] as const;

export function PathSelector({ value, onChange }: PathSelectorProps) {
  const handleChange = async (path: string) => {
    onChange(path);
    try {
      await fetch("/api/ingestion/config/deployment-path", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-Graph-Scope": "all",
        },
        body: JSON.stringify({ deployment_path: path }),
      });
    } catch {
      // Best-effort — readiness gate will show the current state
    }
  };

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">{INGESTION_COPY.pathSelectorHeading}</h3>
      <div className="flex gap-3">
        {PATHS.map((p) => (
          <button
            key={p.value}
            onClick={() => handleChange(p.value)}
            className={`rounded-md border px-4 py-2 text-sm ${
              value === p.value
                ? "border-blue-600 bg-blue-50 text-blue-700"
                : "border-gray-300 hover:bg-gray-50"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
