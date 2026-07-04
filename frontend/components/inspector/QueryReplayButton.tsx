"use client";

import { usePostRetrievalQuery } from "@/lib/query/retrieval";
import { useInspectorStore } from "@/lib/state/inspector-store";

export function QueryReplayButton() {
  const mutation = usePostRetrievalQuery();
  const lastQuery = useInspectorStore((s) => s.lastQuery);
  const setSource = useInspectorStore((s) => s.setSource);

  const onClick = () => {
    if (!lastQuery) return;
    setSource("replay_button");
    mutation.mutate(lastQuery);
  };

  return (
    <button
      type="button"
      data-testid="query-replay-button"
      onClick={onClick}
      disabled={!lastQuery || mutation.isPending}
      className="rounded-md bg-indigo-600 text-white px-3 py-1 text-xs font-medium disabled:opacity-50"
    >
      {mutation.isPending ? "Replaying…" : "Replay query"}
    </button>
  );
}
