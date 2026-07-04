// Chunk 38 D295 / D293 — user-facing strings for the Change Directive
// authoring drawer and EvidenceCriterion sub-form.
//
// Copy must pass the EC-12 forbidden-token scan. The scan rejects the
// eight tokens listed in `_RECON_FORBIDDEN_TOKENS`. Choose framing that
// names the directive as an *intentional* organizational change rather
// than a corrective characterization of past data.

export const FORBIDDEN_TOKENS = [
  "drift",
  "blind spot",
  "mistake",
  "wrong",
  "reality gap",
  "incorrect",
  "failure",
  "deficit",
] as const;

export const COPY = {
  flagButton: {
    label: "Flag as Intentional Change",
    title: "Flag as Intentional Change",
    description:
      "Record an organizational change so the system can frame future evidence as change-in-flight rather than divergence.",
  },
  authoringForm: {
    titleLabel: "Title",
    descriptionLabel: "Description",
    affectedSegmentsLabel: "Affected segments",
    visibilityLabel: "Visibility",
    tierLabel: "Tier",
    operationalLabel: "Operational Adjustment",
    strategicLabel: "Strategic Initiative",
    effectiveDateLabel: "Effective date",
    targetStateLabel: "Target state description",
    realizationHorizonLabel: "Realization horizon",
    responsibleExecutiveLabel: "Responsible executive",
    saveDraftLabel: "Save as draft",
    cancelLabel: "Cancel",
    visibilityOptions: {
      permission_matrix_default: "Permission matrix default",
      private_to_self: "Private to me",
      private_to_named_list: "Private to named list",
      scoped_to_role_cluster: "Scoped to role cluster",
    },
  },
  criterionForm: {
    naturalLanguageLabel: "Evidence criterion (natural language)",
    submitLabel: "Compile to query",
    proposedQueryLabel: "Proposed query",
    approveLabel: "Approve",
    editLabel: "Edit query",
    manualOverrideLabel: "Use manual query",
    saveEditLabel: "Save query",
  },
} as const;
