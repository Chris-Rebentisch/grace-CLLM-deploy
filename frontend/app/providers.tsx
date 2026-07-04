"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEffect, useState } from "react";
import { createQueryClient } from "@/lib/query/query-client";
import { startPhaseController } from "@/lib/phase/phase-controller";
import { startTelemetryBridge } from "@/lib/telemetry/bridge";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => createQueryClient());

  useEffect(() => {
    const unsubBridge = startTelemetryBridge();
    const unsubPhase = startPhaseController();
    return () => {
      unsubPhase();
      unsubBridge();
    };
  }, []);

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
    </QueryClientProvider>
  );
}
