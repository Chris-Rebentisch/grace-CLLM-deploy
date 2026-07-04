"use client";

import { useState } from "react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface OAuthPasteFieldProps {
  sourceId: string;
  provider: "exchange" | "gmail";
  onSuccess?: () => void;
}

/**
 * Paste-URL textarea fallback for headless OAuth consent (Chunk 57).
 *
 * The authorize URL is fetched from the backend and displayed. After
 * the user completes the OAuth flow, they paste the full callback URL
 * containing the authorization code. The component extracts code and
 * state from the URL and POSTs to the backend callback endpoint.
 */
export function OAuthPasteField({
  sourceId,
  provider,
  onSuccess,
}: OAuthPasteFieldProps) {
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [state, setState] = useState<string | null>(null);
  const [callbackUrl, setCallbackUrl] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const initOAuth = async () => {
    setError(null);
    try {
      const resp = await fetch(
        `/api/ingestion/oauth/init/${provider}?source_id=${sourceId}`,
        { headers: { "X-Graph-Scope": "all" } },
      );
      if (!resp.ok) {
        setError(INGESTION_COPY.oauthInitFailed);
        return;
      }
      const data = await resp.json();
      setAuthorizeUrl(data.authorize_url);
      setState(data.state);
    } catch {
      setError(INGESTION_COPY.oauthInitFailed);
    }
  };

  const submitCallback = async () => {
    setError(null);
    setStatus(null);

    if (!state) {
      setError(INGESTION_COPY.oauthStateExpired);
      return;
    }

    // Extract code from pasted URL
    let code: string | null = null;
    try {
      const url = new URL(callbackUrl);
      code = url.searchParams.get("code");
    } catch {
      setError(INGESTION_COPY.oauthInvalidUrl);
      return;
    }

    if (!code) {
      setError(INGESTION_COPY.oauthNoCode);
      return;
    }

    try {
      const resp = await fetch("/api/ingestion/oauth/callback", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Graph-Scope": "all",
        },
        body: JSON.stringify({
          provider,
          code,
          state,
          source_id: sourceId,
        }),
      });
      if (resp.ok) {
        setStatus(INGESTION_COPY.oauthSuccess);
        onSuccess?.();
      } else {
        const data = await resp.json();
        setError(data.detail || INGESTION_COPY.oauthCallbackFailed);
      }
    } catch {
      setError(INGESTION_COPY.oauthCallbackFailed);
    }
  };

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">
        {INGESTION_COPY.oauthHeading}
      </h3>

      {!authorizeUrl ? (
        <button
          onClick={initOAuth}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
        >
          {INGESTION_COPY.oauthInitButton}
        </button>
      ) : (
        <>
          <div className="space-y-2">
            <p className="text-xs text-gray-500">
              {INGESTION_COPY.oauthInstructions}
            </p>
            <a
              href={authorizeUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block truncate text-xs text-blue-600 underline"
            >
              {authorizeUrl}
            </a>
          </div>

          <textarea
            value={callbackUrl}
            onChange={(e) => setCallbackUrl(e.target.value)}
            placeholder={INGESTION_COPY.oauthPastePlaceholder}
            className="w-full rounded border p-2 text-xs"
            rows={3}
          />

          <button
            onClick={submitCallback}
            disabled={!callbackUrl.trim()}
            className="rounded bg-green-600 px-3 py-1.5 text-sm text-white hover:bg-green-700 disabled:opacity-50"
          >
            {INGESTION_COPY.oauthSubmitButton}
          </button>
        </>
      )}

      {status && (
        <p className="text-sm text-green-600">{status}</p>
      )}
      {error && (
        <p className="text-sm text-red-600">{error}</p>
      )}
    </div>
  );
}
