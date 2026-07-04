"use client";

import { useState } from "react";
import { COMMUNICATIONS_COPY } from "@/lib/communications/copy";

interface DpiaAttestationFormProps {
  templateSha256: string;
  onSuccess?: (data: { path: string; valid_until: string }) => void;
}

export function DpiaAttestationForm({
  templateSha256,
  onSuccess,
}: DpiaAttestationFormProps) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [date, setDate] = useState(
    new Date().toISOString().slice(0, 10) + "T00:00:00Z"
  );
  const [status, setStatus] = useState<
    "idle" | "submitting" | "success" | "error"
  >("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [validUntil, setValidUntil] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus("submitting");
    setErrorMessage(null);

    try {
      const resp = await fetch("/api/communications/dpia/attestation", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Graph-Scope": "all",
          "X-Admin-Key": "", // Browser-side: loopback bypass on localhost
        },
        body: JSON.stringify({
          signed_by: name,
          signed_role: role,
          signed_at_iso: date,
          dpia_template_content_sha256: templateSha256,
        }),
      });

      if (resp.status === 201) {
        const data = await resp.json();
        setStatus("success");
        setValidUntil(data.valid_until);
        onSuccess?.(data);
      } else if (resp.status === 409) {
        const data = await resp.json();
        setStatus("error");
        if (data.detail?.includes("already exists")) {
          setErrorMessage(COMMUNICATIONS_COPY.duplicateError);
        } else if (data.detail?.includes("template changed")) {
          setErrorMessage(COMMUNICATIONS_COPY.templateChangedError);
        } else {
          setErrorMessage(data.detail ?? "Conflict");
        }
      } else {
        setStatus("error");
        setErrorMessage("Submission failed");
      }
    } catch {
      setStatus("error");
      setErrorMessage("Network error");
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-lg font-medium">{COMMUNICATIONS_COPY.signForm}</h2>

      <div>
        <label className="block text-sm font-medium text-gray-700">
          {COMMUNICATIONS_COPY.nameField}
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          className="mt-1 block w-full rounded border-gray-300 px-3 py-2 text-sm"
          data-testid="signer-name"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700">
          {COMMUNICATIONS_COPY.roleField}
        </label>
        <input
          type="text"
          value={role}
          onChange={(e) => setRole(e.target.value)}
          required
          className="mt-1 block w-full rounded border-gray-300 px-3 py-2 text-sm"
          data-testid="signer-role"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700">
          {COMMUNICATIONS_COPY.dateField}
        </label>
        <input
          type="date"
          value={date.slice(0, 10)}
          onChange={(e) => setDate(e.target.value + "T00:00:00Z")}
          className="mt-1 block w-full rounded border-gray-300 px-3 py-2 text-sm"
          data-testid="signing-date"
        />
      </div>

      <button
        type="submit"
        disabled={status === "submitting"}
        className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        data-testid="submit-attestation"
      >
        {COMMUNICATIONS_COPY.submitButton}
      </button>

      {status === "success" && (
        <p className="text-sm text-green-600" data-testid="success-message">
          {COMMUNICATIONS_COPY.successMessage}
          {validUntil && ` ${COMMUNICATIONS_COPY.validUntilLabel}: ${validUntil}`}
        </p>
      )}

      {status === "error" && errorMessage && (
        <p className="text-sm text-red-600" data-testid="error-message">
          {errorMessage}
        </p>
      )}
    </form>
  );
}
