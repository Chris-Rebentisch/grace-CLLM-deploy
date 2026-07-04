"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { INGESTION_COPY } from "@/lib/ingestion/copy";
import { COMMUNICATIONS_COPY } from "@/lib/communications/copy";
import { PathSelector } from "@/components/ingestion/PathSelector";
import { SourceTypeSelector } from "@/components/ingestion/SourceTypeSelector";
import { SourceConfigForm } from "@/components/ingestion/SourceConfigForm";
import { TestConnectionButton } from "@/components/ingestion/TestConnectionButton";
import { ReadinessGate } from "@/components/ingestion/ReadinessGate";
import { OAuthPasteField } from "@/components/ingestion/OAuthPasteField";
import { ScheduleConfigFields } from "@/components/ingestion/ScheduleConfigFields";

const LIVE_SOURCE_TYPES = new Set(["imap", "exchange", "gmail"]);

export default function IngestionSetupPage() {
  const [deploymentPath, setDeploymentPath] = useState<string | null>(null);
  const [sourceType, setSourceType] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [triggerStatus, setTriggerStatus] = useState<string | null>(null);

  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleMode, setScheduleMode] = useState("interval");
  const [scheduleIntervalHours, setScheduleIntervalHours] = useState(6);

  useEffect(() => {
    setScheduleEnabled(false);
    setScheduleMode("interval");
    setScheduleIntervalHours(6);
  }, [sourceType]);

  const isLiveType = sourceType !== null && LIVE_SOURCE_TYPES.has(sourceType);

  const handleCreateSource = async (config: Record<string, string>) => {
    if (!sourceType) return;
    const { segment, ...rest } = config;

    const liveSchedule: Record<string, unknown> = {};
    if (LIVE_SOURCE_TYPES.has(sourceType)) {
      liveSchedule.schedule_enabled = scheduleEnabled;
      if (scheduleEnabled) {
        liveSchedule.schedule_mode = scheduleMode;
        if (scheduleMode === "interval") {
          liveSchedule.schedule_interval_hours = scheduleIntervalHours;
        }
      }
    }

    try {
      const resp = await fetch("/api/ingestion/sources", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Graph-Scope": "all",
        },
        body: JSON.stringify({
          name: `${sourceType}-${Date.now()}`,
          source_type: sourceType,
          config_json: {
            source_type: sourceType,
            ...rest,
            ...liveSchedule,
          },
          segment: segment || "default",
        }),
      });
      if (resp.ok) {
        const data = await resp.json();
        setSourceId(data.id);
      }
    } catch {
      // Errors surfaced via test-connection
    }
  };

  const handleTrigger = async () => {
    if (!sourceId) return;
    try {
      const resp = await fetch(`/api/ingestion/sources/${sourceId}/run`, {
        method: "POST",
        headers: { "X-Graph-Scope": "all" },
      });
      if (resp.status === 202) {
        setTriggerStatus(INGESTION_COPY.triggerSuccess);
      } else {
        const data = await resp.json();
        setTriggerStatus(data.detail ?? "Failed to start run");
      }
    } catch {
      setTriggerStatus("Network error");
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-8 p-6">
      <div>
        <h1 className="text-xl font-semibold">{INGESTION_COPY.pageTitle}</h1>
        <p className="mt-1 text-sm text-gray-600">{INGESTION_COPY.pageDescription}</p>
      </div>

      <div className="text-sm">
        <Link
          href="/communications/profiles/settings"
          className="text-blue-600 hover:underline"
        >
          {COMMUNICATIONS_COPY.navLink}
        </Link>
      </div>

      <PathSelector value={deploymentPath} onChange={setDeploymentPath} />

      {deploymentPath && (
        <SourceTypeSelector value={sourceType} onChange={setSourceType} />
      )}

      {sourceType && (
        <>
          {isLiveType && (
            <ScheduleConfigFields
              scheduleEnabled={scheduleEnabled}
              scheduleMode={scheduleMode}
              scheduleIntervalHours={scheduleIntervalHours}
              onScheduleEnabledChange={setScheduleEnabled}
              onScheduleModeChange={setScheduleMode}
              onScheduleIntervalChange={setScheduleIntervalHours}
            />
          )}
          <SourceConfigForm sourceType={sourceType} onSubmit={handleCreateSource} />
        </>
      )}

      {sourceId && (
        <>
          <TestConnectionButton sourceId={sourceId} />
          <ReadinessGate />
          {(sourceType === "exchange" || sourceType === "gmail") && (
            <OAuthPasteField
              sourceId={sourceId}
              provider={sourceType}
              onSuccess={() => {}}
            />
          )}
          <button
            onClick={handleTrigger}
            className="rounded bg-green-600 px-4 py-2 text-sm text-white hover:bg-green-700"
          >
            {INGESTION_COPY.triggerButton}
          </button>
          {triggerStatus && (
            <p className="text-sm text-gray-600">{triggerStatus}</p>
          )}
        </>
      )}
    </div>
  );
}
