export function InspectorEmptyState() {
  return (
    <div
      data-testid="inspector-empty-state"
      className="flex items-center justify-center h-full text-sm text-slate-500 p-12 text-center"
    >
      <div>
        <p className="font-medium">
          No retrieval query has been run in this session.
        </p>
        <p className="text-xs mt-2 text-slate-400">
          Submit a query from the chat surface, then click “View retrieval
          trace” to populate this inspector.
        </p>
      </div>
    </div>
  );
}
