"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { apiRequest } from "@/lib/api/client";
import { COPY } from "@/lib/change-directives/copy";
import type {
  ChangeDirectiveCreateRequest,
  VisibilityMode,
} from "@/lib/api/types";

type Tier = "Operational_Adjustment" | "Strategic_Initiative";

export type ChangeDirectiveAuthoringFormProps = {
  sessionId: string | null;
  flaggedFromElementName: string | null;
  defaultAffectedSegments?: string[];
  onCreated?: (directiveId: string) => void;
  onCancel?: () => void;
};

export function ChangeDirectiveAuthoringForm({
  sessionId,
  flaggedFromElementName,
  defaultAffectedSegments = [],
  onCreated,
  onCancel,
}: ChangeDirectiveAuthoringFormProps) {
  const [tier, setTier] = React.useState<Tier>("Operational_Adjustment");
  const [title, setTitle] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [segmentsText, setSegmentsText] = React.useState(
    defaultAffectedSegments.join(", "),
  );
  const [visibility, setVisibility] = React.useState<VisibilityMode>(
    "permission_matrix_default",
  );
  const [effectiveDate, setEffectiveDate] = React.useState("");
  const [targetState, setTargetState] = React.useState("");
  const [horizon, setHorizon] = React.useState("");
  const [executive, setExecutive] = React.useState("");
  const [criterion, setCriterion] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const segments = segmentsText
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
      const body: ChangeDirectiveCreateRequest = {
        tier,
        title: title.slice(0, 200),
        description,
        affected_segments: segments,
        visibility,
        flagged_from_session_id: sessionId,
        flagged_from_element_name: flaggedFromElementName,
      };
      if (tier === "Operational_Adjustment") {
        body.effective_date = effectiveDate || null;
      } else {
        body.target_state_description = targetState;
        body.realization_horizon = horizon || null;
        body.responsible_executive = executive || null;
        if (criterion.trim().length > 0) {
          body.initial_evidence_criteria = [criterion.trim()];
        }
      }
      const created = await apiRequest<{ directive_id: string }>(
        "/api/change-directives",
        { method: "POST", body },
      );
      onCreated?.(created.directive_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="cd-authoring-form"
      className="space-y-3"
    >
      <fieldset className="flex gap-3" data-testid="tier-toggle">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="tier"
            value="Operational_Adjustment"
            checked={tier === "Operational_Adjustment"}
            onChange={() => setTier("Operational_Adjustment")}
            data-testid="tier-oa"
          />
          {COPY.authoringForm.operationalLabel}
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="tier"
            value="Strategic_Initiative"
            checked={tier === "Strategic_Initiative"}
            onChange={() => setTier("Strategic_Initiative")}
            data-testid="tier-si"
          />
          {COPY.authoringForm.strategicLabel}
        </label>
      </fieldset>
      <label className="block text-sm font-medium">
        {COPY.authoringForm.titleLabel}
        <input
          required
          maxLength={200}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          data-testid="cd-title"
          className="mt-1 block w-full rounded border border-input bg-transparent px-2 py-1 text-sm"
        />
      </label>
      <label className="block text-sm font-medium">
        {COPY.authoringForm.descriptionLabel}
        <Textarea
          required
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          data-testid="cd-description"
        />
      </label>
      <label className="block text-sm font-medium">
        {COPY.authoringForm.affectedSegmentsLabel}
        <input
          value={segmentsText}
          onChange={(e) => setSegmentsText(e.target.value)}
          data-testid="cd-segments"
          className="mt-1 block w-full rounded border border-input bg-transparent px-2 py-1 text-sm"
        />
      </label>
      <fieldset
        data-testid="visibility-radio"
        className="flex flex-col gap-1 text-sm"
      >
        <legend className="font-medium">
          {COPY.authoringForm.visibilityLabel}
        </legend>
        {(
          Object.keys(
            COPY.authoringForm.visibilityOptions,
          ) as VisibilityMode[]
        ).map((mode) => (
          <label key={mode} className="flex items-center gap-2">
            <input
              type="radio"
              name="visibility"
              value={mode}
              checked={visibility === mode}
              onChange={() => setVisibility(mode)}
            />
            {COPY.authoringForm.visibilityOptions[mode]}
          </label>
        ))}
      </fieldset>
      {tier === "Operational_Adjustment" ? (
        <label className="block text-sm font-medium">
          {COPY.authoringForm.effectiveDateLabel}
          <input
            type="date"
            value={effectiveDate}
            onChange={(e) => setEffectiveDate(e.target.value)}
            data-testid="cd-effective-date"
            className="mt-1 block w-full rounded border border-input bg-transparent px-2 py-1 text-sm"
          />
        </label>
      ) : (
        <div className="space-y-3" data-testid="cd-strategic-fields">
          <label className="block text-sm font-medium">
            {COPY.authoringForm.targetStateLabel}
            <Textarea
              required
              value={targetState}
              onChange={(e) => setTargetState(e.target.value)}
              data-testid="cd-target-state"
            />
          </label>
          <label className="block text-sm font-medium">
            {COPY.authoringForm.realizationHorizonLabel}
            <input
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
              data-testid="cd-horizon"
              className="mt-1 block w-full rounded border border-input bg-transparent px-2 py-1 text-sm"
            />
          </label>
          <label className="block text-sm font-medium">
            {COPY.authoringForm.responsibleExecutiveLabel}
            <input
              value={executive}
              onChange={(e) => setExecutive(e.target.value)}
              data-testid="cd-executive"
              className="mt-1 block w-full rounded border border-input bg-transparent px-2 py-1 text-sm"
            />
          </label>
          <label className="block text-sm font-medium">
            {COPY.criterionForm.naturalLanguageLabel}
            <Textarea
              required
              value={criterion}
              onChange={(e) => setCriterion(e.target.value)}
              data-testid="cd-initial-criterion"
            />
          </label>
        </div>
      )}
      {error && (
        <div role="alert" className="text-sm text-destructive">
          {error}
        </div>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={busy} data-testid="cd-submit">
          {COPY.authoringForm.saveDraftLabel}
        </Button>
        {onCancel && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onCancel}
            data-testid="cd-cancel"
          >
            {COPY.authoringForm.cancelLabel}
          </Button>
        )}
      </div>
    </form>
  );
}
