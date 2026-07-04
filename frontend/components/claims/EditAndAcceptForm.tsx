"use client";
import { useState } from "react";
import type { ClaimRecord, AcceptClaimModified } from "@/lib/api/types";

export function EditAndAcceptForm({
  claim,
  onSubmit,
  onCancel,
}: {
  claim: ClaimRecord;
  onSubmit: (mod: AcceptClaimModified) => void;
  onCancel: () => void;
}) {
  const [subject, setSubject] = useState(claim.subject_name);
  const [predicate, setPredicate] = useState(claim.predicate ?? "");
  const [objectName, setObjectName] = useState(claim.object_name ?? "");
  const [propsJson, setPropsJson] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = () => {
    let parsedProps: Record<string, unknown> | null = null;
    if (propsJson.trim()) {
      try {
        parsedProps = JSON.parse(propsJson);
      } catch {
        setError("properties_json must be valid JSON");
        return;
      }
    }
    setError(null);
    onSubmit({
      subject_name: subject,
      predicate: predicate || null,
      object_name: objectName || null,
      properties_json: parsedProps,
    });
  };

  return (
    <form
      data-testid="edit-and-accept-form"
      className="space-y-2 rounded border border-blue-300 bg-blue-50 p-3 text-xs"
      onSubmit={(e) => {
        e.preventDefault();
        handleSubmit();
      }}
    >
      <label className="block">
        Subject
        <input
          data-testid="edit-subject"
          className="mt-1 w-full rounded border px-2 py-1"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
      </label>
      <label className="block">
        Predicate
        <input
          data-testid="edit-predicate"
          className="mt-1 w-full rounded border px-2 py-1"
          value={predicate}
          onChange={(e) => setPredicate(e.target.value)}
        />
      </label>
      <label className="block">
        Object
        <input
          data-testid="edit-object"
          className="mt-1 w-full rounded border px-2 py-1"
          value={objectName}
          onChange={(e) => setObjectName(e.target.value)}
        />
      </label>
      <details className="text-slate-700">
        <summary className="cursor-pointer">Edit properties (JSON)</summary>
        <textarea
          data-testid="edit-properties-json"
          className="mt-1 h-24 w-full rounded border px-2 py-1 font-mono"
          value={propsJson}
          onChange={(e) => setPropsJson(e.target.value)}
          placeholder='{"key": "value"}'
        />
      </details>
      {error && (
        <p data-testid="edit-error" className="text-red-700">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          data-testid="edit-submit"
          className="rounded bg-blue-700 px-3 py-1 text-white"
        >
          Save and accept
        </button>
        <button
          type="button"
          data-testid="edit-cancel"
          className="rounded border px-3 py-1"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
