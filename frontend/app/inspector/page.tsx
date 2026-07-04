"use client";

import { Suspense } from "react";
import { RetrievalInspector } from "@/components/inspector/RetrievalInspector";

export default function InspectorPage() {
  return (
    <div className="h-screen w-screen overflow-hidden">
      <Suspense
        fallback={
          <div className="flex items-center justify-center h-full text-sm text-slate-500">
            Loading inspector…
          </div>
        }
      >
        <RetrievalInspector />
      </Suspense>
    </div>
  );
}
