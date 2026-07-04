/** Sample-size advisory bands — UI-only per spec D432 (no server enforcement). */

export type SampleSizeGuidanceKind =
  | "warning_low"
  | "neutral"
  | "representative"
  | "warning_high";

export function getSampleSizeGuidance(
  count: number,
): SampleSizeGuidanceKind {
  if (count < 200) return "warning_low";
  if (count < 500) return "neutral";
  if (count < 1000) return "representative";
  return "warning_high";
}
