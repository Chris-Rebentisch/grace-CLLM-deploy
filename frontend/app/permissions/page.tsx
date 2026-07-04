"use client";

/**
 * /permissions — active matrix + version list (Chunk 42, D331).
 *
 * Reads the active matrix from `GET /api/permissions/matrix/active` and
 * lists recent versions from `GET /api/permissions/matrix/versions`.
 * No client-side hash mutation — the server is the sole writer (D331).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { permissionsApi } from "@/lib/api/permissions";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";
import type {
  PermissionMatrixListResponse,
  PermissionMatrixVersion,
} from "@/lib/api/types";

export default function PermissionsPage() {
  const [active, setActive] = useState<PermissionMatrixVersion | null>(null);
  const [versions, setVersions] = useState<PermissionMatrixListResponse | null>(
    null,
  );
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [a, vs] = await Promise.all([
          permissionsApi.getActiveMatrix().catch(() => null),
          permissionsApi.listMatrixVersions(25).catch(() => null),
        ]);
        if (cancelled) return;
        setActive(a);
        setVersions(vs);
      } catch (e) {
        if (!cancelled)
          setErr(e instanceof Error ? e.message : "Failed to load matrices");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main
      data-testid="permissions-page"
      className="mx-auto flex max-w-4xl flex-col gap-4 p-4"
    >
      <h1 className="text-lg font-semibold text-slate-900">
        {PERMISSIONS_COPY.pageTitle}
      </h1>

      <section
        data-testid="permissions-active-matrix"
        className="rounded-md border border-slate-200 bg-white p-3"
      >
        <h2 className="mb-2 text-sm font-semibold text-slate-900">
          {PERMISSIONS_COPY.activeMatrixHeading}
        </h2>
        {loading ? (
          <p className="text-xs text-slate-500">Loading…</p>
        ) : err ? (
          <p
            data-testid="permissions-active-error"
            className="text-xs text-rose-700"
          >
            {err}
          </p>
        ) : active ? (
          <dl className="grid grid-cols-2 gap-1 text-xs text-slate-800">
            <dt className="text-slate-500">Matrix id</dt>
            <dd className="font-mono">{active.permission_matrix_id}</dd>
            <dt className="text-slate-500">Version</dt>
            <dd>{active.version_label ?? "—"}</dd>
            <dt className="text-slate-500">Hash</dt>
            <dd className="font-mono">{active.payload_hash.slice(0, 16)}…</dd>
            <dt className="text-slate-500">Ratified at</dt>
            <dd>{active.created_at}</dd>
          </dl>
        ) : (
          <p
            data-testid="permissions-no-active"
            className="text-xs text-slate-700"
          >
            {PERMISSIONS_COPY.noActiveMatrix}
          </p>
        )}
      </section>

      <section
        data-testid="permissions-version-list"
        className="rounded-md border border-slate-200 bg-white p-3"
      >
        <h2 className="mb-2 text-sm font-semibold text-slate-900">
          Version history
        </h2>
        {versions && versions.versions.length > 0 ? (
          <ul className="flex flex-col gap-1">
            {versions.versions.map((v) => (
              <li
                key={v.permission_matrix_id}
                data-testid={`permissions-version-row-${v.permission_matrix_id}`}
                className="flex items-center justify-between rounded border border-slate-200 px-2 py-1 text-[11px]"
              >
                <span className="font-mono">{v.payload_hash.slice(0, 12)}…</span>
                <span className="text-slate-600">
                  {v.version_label ?? "—"} · {v.created_at}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs italic text-slate-500">
            No matrix versions ratified yet.
          </p>
        )}
      </section>

      <section className="rounded-md border border-slate-200 bg-white p-3">
        <h2 className="mb-2 text-sm font-semibold text-slate-900">Tools</h2>
        <ul className="flex flex-col gap-1 text-xs">
          <li>
            <Link
              href="/permissions/drift"
              className="text-slate-700 hover:underline"
            >
              {PERMISSIONS_COPY.driftQueueHeading}
            </Link>
          </li>
        </ul>
      </section>
    </main>
  );
}
