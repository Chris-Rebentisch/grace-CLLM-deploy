"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { apiRequest } from "@/lib/api/client";
import { COPY } from "@/lib/change-directives/copy";
import type {
  CriterionCreateRequest,
  CriterionPatchRequest,
  EvidenceCriterion,
} from "@/lib/api/types";

export type EvidenceCriterionSubFormProps = {
  directiveId: string;
  initialCriterion?: EvidenceCriterion | null;
  onCriterionChanged?: (criterion: EvidenceCriterion) => void;
};

type Mode = "compose" | "review" | "edit" | "manual";

export function EvidenceCriterionSubForm({
  directiveId,
  initialCriterion = null,
  onCriterionChanged,
}: EvidenceCriterionSubFormProps) {
  const [criterion, setCriterion] = React.useState<EvidenceCriterion | null>(
    initialCriterion,
  );
  const [naturalLanguage, setNaturalLanguage] = React.useState("");
  const [editedQuery, setEditedQuery] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const mode: Mode = React.useMemo(() => {
    if (!criterion) return "compose";
    if (criterion.compilation_status === "approved") return "review";
    return "review";
  }, [criterion]);

  async function handleCompile() {
    setBusy(true);
    try {
      const body: CriterionCreateRequest = { natural_language: naturalLanguage };
      const created = await apiRequest<EvidenceCriterion>(
        `/api/change-directives/${directiveId}/criteria`,
        { method: "POST", body },
      );
      setCriterion(created);
      setEditedQuery(created.compiled_query ?? "");
      onCriterionChanged?.(created);
    } finally {
      setBusy(false);
    }
  }

  async function patch(action: CriterionPatchRequest["action"], compiled?: string) {
    if (!criterion) return;
    setBusy(true);
    try {
      const body: CriterionPatchRequest = {
        action,
        compiled_query: compiled ?? null,
      };
      const updated = await apiRequest<EvidenceCriterion>(
        `/api/change-directives/${directiveId}/criteria/${criterion.criterion_id}`,
        { method: "POST", body },
      );
      setCriterion(updated);
      onCriterionChanged?.(updated);
    } finally {
      setBusy(false);
    }
  }

  if (mode === "compose") {
    return (
      <div data-testid="criterion-compose" className="space-y-2">
        <label className="text-sm font-medium">
          {COPY.criterionForm.naturalLanguageLabel}
        </label>
        <Textarea
          value={naturalLanguage}
          onChange={(e) => setNaturalLanguage(e.target.value)}
          aria-label={COPY.criterionForm.naturalLanguageLabel}
        />
        <Button
          type="button"
          onClick={handleCompile}
          disabled={busy || naturalLanguage.trim().length === 0}
        >
          {COPY.criterionForm.submitLabel}
        </Button>
      </div>
    );
  }

  return (
    <div data-testid="criterion-review" className="space-y-2">
      <div>
        <div className="text-sm font-medium">
          {COPY.criterionForm.proposedQueryLabel}
        </div>
        <pre
          data-testid="proposed-query"
          className="rounded border border-border bg-muted/30 p-2 text-xs"
        >
          {criterion?.compiled_query ?? ""}
        </pre>
      </div>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="default"
          disabled={busy}
          onClick={() => patch("approve")}
          data-testid="criterion-approve"
        >
          {COPY.criterionForm.approveLabel}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => {
            setEditedQuery(criterion?.compiled_query ?? "");
          }}
          data-testid="criterion-edit"
        >
          {COPY.criterionForm.editLabel}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => patch("manual_override", editedQuery)}
          data-testid="criterion-manual"
        >
          {COPY.criterionForm.manualOverrideLabel}
        </Button>
      </div>
      <Textarea
        value={editedQuery}
        onChange={(e) => setEditedQuery(e.target.value)}
        aria-label="Edit query"
        data-testid="criterion-query-editor"
      />
      <Button
        type="button"
        variant="secondary"
        disabled={busy || editedQuery.trim().length === 0}
        onClick={() => patch("edit", editedQuery)}
        data-testid="criterion-save-edit"
      >
        {COPY.criterionForm.saveEditLabel}
      </Button>
    </div>
  );
}
