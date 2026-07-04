"use client";

import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface ScheduleConfigFieldsProps {
  scheduleEnabled: boolean;
  scheduleMode: string;
  scheduleIntervalHours: number;
  onScheduleEnabledChange: (enabled: boolean) => void;
  onScheduleModeChange: (mode: string) => void;
  onScheduleIntervalChange: (hours: number) => void;
}

/**
 * Schedule mode + interval config fields for source setup (Chunk 57).
 */
export function ScheduleConfigFields({
  scheduleEnabled,
  scheduleMode,
  scheduleIntervalHours,
  onScheduleEnabledChange,
  onScheduleModeChange,
  onScheduleIntervalChange,
}: ScheduleConfigFieldsProps) {
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">
        {INGESTION_COPY.scheduleHeading}
      </h3>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={scheduleEnabled}
          onChange={(e) => onScheduleEnabledChange(e.target.checked)}
          className="rounded"
        />
        {INGESTION_COPY.scheduleEnabledLabel}
      </label>

      {scheduleEnabled && (
        <>
          <div className="space-y-1">
            <label className="block text-xs text-gray-500">
              {INGESTION_COPY.scheduleModeLabel}
            </label>
            <select
              value={scheduleMode}
              onChange={(e) => onScheduleModeChange(e.target.value)}
              className="rounded border px-2 py-1 text-sm"
            >
              <option value="interval">
                {INGESTION_COPY.scheduleModeInterval}
              </option>
              <option value="one_time">
                {INGESTION_COPY.scheduleModeOneTime}
              </option>
            </select>
          </div>

          {scheduleMode === "interval" && (
            <div className="space-y-1">
              <label className="block text-xs text-gray-500">
                {INGESTION_COPY.scheduleIntervalLabel}
              </label>
              <input
                type="number"
                min={0.25}
                step={0.25}
                value={scheduleIntervalHours}
                onChange={(e) =>
                  onScheduleIntervalChange(parseFloat(e.target.value) || 1)
                }
                className="w-24 rounded border px-2 py-1 text-sm"
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
