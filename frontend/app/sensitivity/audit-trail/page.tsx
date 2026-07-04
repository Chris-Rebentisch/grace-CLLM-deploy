"use client";

/**
 * /sensitivity/audit-trail — query-event audit-trail filter (Chunk 43, CP6).
 *
 * The CP3 backend route returns 200 with empty events; CP5 (D346)
 * lights the body up via the ArcadeDB Query_Event tag property and
 * the mandatory-context cypher rewriter.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { permissionsApi } from "@/lib/api/permissions";
import { SensitivityAuditTrailFilter } from "@/components/sensitivity/SensitivityAuditTrailFilter";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";
import type { PermissionMatrixVersion } from "@/lib/api/types";

export default function SensitivityAuditTrailPage() {
  const [activeMatrix, setActiveMatrix] =
    useState<PermissionMatrixVersion | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const m = await permissionsApi.getActiveMatrix().catch(() => null);
      if (!cancelled) setActiveMatrix(m);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main
      data-testid="sensitivity-audit-trail-page"
      className="mx-auto flex max-w-4xl flex-col gap-3 p-4"
    >
      <header>
        <Link
          href="/sensitivity"
          className="text-[11px] text-slate-700 hover:underline"
        >
          ← {SENSITIVITY_COPY.pageTitle}
        </Link>
        <h1 className="mt-1 text-lg font-semibold text-slate-900">
          {SENSITIVITY_COPY.auditTrailHeading}
        </h1>
      </header>

      <SensitivityAuditTrailFilter
        matrixId={activeMatrix?.permission_matrix_id ?? null}
      />
    </main>
  );
}
