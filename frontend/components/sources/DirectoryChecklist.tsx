"use client";
import type { SourcesScanDirectoryNode } from "@/lib/api/types";

export function DirectoryChecklist({
  directories,
  selected,
  onToggle,
}: {
  directories: SourcesScanDirectoryNode[];
  selected: Set<string>;
  onToggle: (path: string) => void;
}) {
  return (
    <ul data-testid="directory-checklist" className="divide-y rounded border bg-white">
      {directories.map((d) => (
        <li key={d.path} className="flex items-center gap-2 px-2 py-1 text-xs">
          <input
            id={`dir-${d.path}`}
            data-testid={`dir-checkbox-${d.name}`}
            type="checkbox"
            checked={selected.has(d.path)}
            onChange={() => onToggle(d.path)}
          />
          <label htmlFor={`dir-${d.path}`} className="flex-1">
            <span className="font-medium">{d.name}</span>
            <span className="ml-2 text-slate-500">
              {d.document_files} docs / {d.total_files} files
            </span>
          </label>
        </li>
      ))}
    </ul>
  );
}
