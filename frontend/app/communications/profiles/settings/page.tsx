"use client";

import { useEffect, useState } from "react";
import { COMMUNICATIONS_COPY } from "@/lib/communications/copy";
import { DpiaAttestationForm } from "@/components/communications/DpiaAttestationForm";

interface DpiaStatus {
  attestation_active: boolean;
  valid_until: string | null;
  signed_by: string | null;
}

export default function DpiaSettingsPage() {
  const [status, setStatus] = useState<DpiaStatus | null>(null);
  const [templateSha, setTemplateSha] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const resp = await fetch("/api/communications/dpia/status", {
          headers: { "X-Graph-Scope": "all" },
        });
        if (resp.ok) {
          const data = await resp.json();
          setStatus(data);
        }
      } catch {
        // Status fetch failure is non-critical
      }
      setLoading(false);
    };
    fetchStatus();
  }, []);

  useEffect(() => {
    // Compute SHA-256 of the template content at mount (Lock-R4 binding)
    const computeSha = async () => {
      try {
        // In production, fetch template from a known endpoint or embed
        // For now, use a placeholder - the server validates the SHA
        const encoder = new TextEncoder();
        const data = encoder.encode("");
        const hashBuffer = await crypto.subtle.digest("SHA-256", data);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        const hashHex = hashArray
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        setTemplateSha(hashHex);
      } catch {
        // SHA computation failure
      }
    };
    computeSha();
  }, []);

  return (
    <div className="mx-auto max-w-2xl space-y-8 p-6">
      <div>
        <h1 className="text-xl font-semibold">
          {COMMUNICATIONS_COPY.pageTitle}
        </h1>
        <p className="mt-1 text-sm text-gray-600">
          {COMMUNICATIONS_COPY.pageDescription}
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-gray-500">Loading...</p>
      ) : (
        <>
          {/* DPIA status indicator */}
          <div
            className="rounded border p-4"
            data-testid="dpia-status-indicator"
          >
            {status?.attestation_active ? (
              <div className="space-y-1">
                <p className="font-medium text-green-700">
                  {COMMUNICATIONS_COPY.dpiaActiveLabel}
                </p>
                <p className="text-sm text-gray-600">
                  {COMMUNICATIONS_COPY.modeIndividual}
                </p>
                {status.valid_until && (
                  <p className="text-sm text-gray-500">
                    {COMMUNICATIONS_COPY.validUntilLabel}: {status.valid_until}
                  </p>
                )}
                {status.signed_by && (
                  <p className="text-sm text-gray-500">
                    {COMMUNICATIONS_COPY.signedByLabel}: {status.signed_by}
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-1">
                <p className="font-medium text-amber-700">
                  {COMMUNICATIONS_COPY.dpiaInactiveLabel}
                </p>
                <p className="text-sm text-gray-600">
                  {COMMUNICATIONS_COPY.modeAggregate}
                </p>
              </div>
            )}
          </div>

          {/* Attestation form */}
          <DpiaAttestationForm
            templateSha256={templateSha}
            onSuccess={() => {
              // Refresh status after successful submission
              fetch("/api/communications/dpia/status", {
                headers: { "X-Graph-Scope": "all" },
              })
                .then((r) => r.json())
                .then(setStatus)
                .catch(() => {});
            }}
          />
        </>
      )}
    </div>
  );
}
