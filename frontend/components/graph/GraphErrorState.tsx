export type GraphErrorStateProps = {
  message?: string;
};

export function GraphErrorState({ message }: GraphErrorStateProps) {
  return (
    <div
      data-testid="graph-error-state"
      className="flex items-center justify-center h-full text-sm text-red-600 p-12 text-center"
    >
      <div>
        <p className="font-medium">Graph backend unavailable.</p>
        <p className="text-xs mt-2 text-red-400">
          {message ?? "Retry after ArcadeDB becomes reachable."}
        </p>
      </div>
    </div>
  );
}
