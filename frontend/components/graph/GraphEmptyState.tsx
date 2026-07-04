export function GraphEmptyState() {
  return (
    <div
      data-testid="graph-empty-state"
      className="flex items-center justify-center h-full text-sm text-slate-500 p-12 text-center"
    >
      <div>
        <p className="font-medium">No entities in the current scope.</p>
        <p className="text-xs mt-2 text-slate-400">
          Run the extraction pipeline or adjust filters to populate the graph.
        </p>
      </div>
    </div>
  );
}
