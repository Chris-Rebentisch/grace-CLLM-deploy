"use client";
import { useState } from "react";
import { useBrowsePath } from "@/lib/query/sources";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Navigable in-app file browser (Option B). Drills folder-by-folder through the
 * local filesystem; the operator checks individual folders OR individual files.
 * Selection is held by the parent (`selected` set of absolute paths); this
 * component only owns the current-directory navigation state.
 */
export function FileBrowser({
  selected,
  onToggle,
  initialPath,
}: {
  selected: Set<string>;
  onToggle: (path: string) => void;
  initialPath?: string;
}) {
  const [path, setPath] = useState<string | undefined>(initialPath);
  const [jump, setJump] = useState("");
  const { data, isLoading, isError } = useBrowsePath(path);

  return (
    <div data-testid="file-browser" className="rounded border bg-white text-xs">
      {/* Toolbar: up + current path + jump-to-path */}
      <div className="flex items-center gap-2 border-b px-2 py-1">
        <button
          type="button"
          data-testid="file-browser-up"
          disabled={!data?.parent}
          onClick={() => data?.parent && setPath(data.parent)}
          className="rounded border px-2 py-0.5 disabled:opacity-40"
        >
          ↑ Up
        </button>
        <span
          data-testid="file-browser-cwd"
          className="flex-1 truncate font-mono text-slate-600"
          title={data?.path}
        >
          {data?.path ?? "…"}
        </span>
      </div>
      <div className="flex items-center gap-2 border-b px-2 py-1">
        <input
          data-testid="file-browser-jump-input"
          className="flex-1 rounded border px-2 py-0.5"
          placeholder="/absolute/path/to/folder"
          value={jump}
          onChange={(e) => setJump(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && jump) setPath(jump);
          }}
        />
        <button
          type="button"
          data-testid="file-browser-jump"
          onClick={() => jump && setPath(jump)}
          className="rounded bg-slate-800 px-2 py-0.5 text-white"
        >
          Go
        </button>
      </div>

      {isLoading && (
        <p data-testid="file-browser-loading" className="px-2 py-2 text-slate-500">
          Loading…
        </p>
      )}
      {isError && (
        <p className="px-2 py-2 text-rose-600">Failed to read that location.</p>
      )}
      {data?.error && (
        <p data-testid="file-browser-error" className="px-2 py-2 text-rose-600">
          {data.error}
        </p>
      )}

      {data && !data.error && (
        <ul
          data-testid="file-browser-list"
          className="max-h-80 divide-y overflow-auto"
        >
          {data.entries.length === 0 && (
            <li className="px-2 py-2 text-slate-400">Empty folder.</li>
          )}
          {data.entries.map((e) => {
            const checkDisabled = !e.is_dir && !e.supported;
            return (
              <li
                key={e.path}
                className="flex items-center gap-2 px-2 py-1"
                data-testid={`browse-entry-${e.name}`}
              >
                <input
                  type="checkbox"
                  data-testid={`browse-checkbox-${e.name}`}
                  checked={selected.has(e.path)}
                  disabled={checkDisabled}
                  onChange={() => onToggle(e.path)}
                  title={
                    checkDisabled ? "Unsupported file type" : "Include in source set"
                  }
                />
                {e.is_dir ? (
                  <button
                    type="button"
                    data-testid={`browse-open-${e.name}`}
                    onClick={() => setPath(e.path)}
                    className="flex-1 truncate text-left font-medium text-slate-800"
                  >
                    📁 {e.name}
                  </button>
                ) : (
                  <span
                    className={`flex-1 truncate ${
                      e.supported ? "text-slate-700" : "text-slate-400"
                    }`}
                  >
                    📄 {e.name}
                    {!e.supported && (
                      <span className="ml-1 text-slate-400">(unsupported)</span>
                    )}
                  </span>
                )}
                {!e.is_dir && (
                  <span className="text-slate-400">{formatSize(e.size_bytes)}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
