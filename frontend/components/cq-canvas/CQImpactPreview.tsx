"use client";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";

export type CQImpactPreviewProps = {
  sessionId: string;
  elementName: string | null;
  hypotheticalDecision: string | null;
};

export function CQImpactPreview({ sessionId, elementName, hypotheticalDecision }: CQImpactPreviewProps) {
  const { data, isPlaceholderData, isFetching } = useQuery({
    queryKey: ["cq-impact-preview", sessionId, elementName, hypotheticalDecision],
    queryFn: () => apiClient.getCQImpactPreview(sessionId, elementName!, hypotheticalDecision!),
    enabled: !!elementName && !!hypotheticalDecision,
    placeholderData: (prev) => prev,
    refetchOnWindowFocus: false,
  });

  if (!elementName || !hypotheticalDecision) return null;

  return (
    <div data-testid="cq-impact-preview" className={`rounded-md border p-2 text-xs ${isPlaceholderData ? "opacity-50" : ""}`}>
      <div className="font-medium text-slate-700">
        Impact Preview: {elementName} → {hypotheticalDecision}
      </div>
      {data && (
        <div className="mt-1 text-slate-500">
          <div>CQs affected: {(data as Record<string,unknown>).cqs_affected ? ((data as Record<string,unknown>).cqs_affected as unknown[]).length : 0}</div>
        </div>
      )}
      {isFetching && <div className="mt-1 text-[10px] text-slate-400">Updating...</div>}
    </div>
  );
}
