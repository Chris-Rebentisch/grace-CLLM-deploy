"use client";

/**
 * CurationEmailList — email selector with sender filter, date-range filter,
 * and selection preview pane (Chunk 60, CP6).
 *
 * Message selector operates on selected_message_ids (research §6 Q5 resolved).
 */

import { useMemo, useState } from "react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface EventItem {
  event_id: string;
  message_id: string;
  sender_email: string;
  sender_display_name: string | null;
  subject: string;
  sent_at: string | null;
  received_at: string | null;
  triage_tier_outcome: string;
}

interface DiversityMetrics {
  sender_band: "narrow" | "balanced" | "wide";
  sender_count: number;
  thread_depth_band: string;
  thread_count: number;
  date_range_band: string;
  date_span_days: number;
}

const SENDER_BAND_LABELS: Record<string, string> = {
  narrow: "Few senders",
  balanced: "Balanced mix",
  wide: "Many senders",
};

interface CurationEmailListProps {
  events: EventItem[];
  selected: Set<string>;
  onToggle: (messageId: string) => void;
  onSelectAll: () => void;
  diversityMetrics?: DiversityMetrics | null;
}

export function CurationEmailList({
  events,
  selected,
  onToggle,
  onSelectAll,
  diversityMetrics,
}: CurationEmailListProps) {
  const [senderFilter, setSenderFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  // Sender filter (client-side)
  const filteredEvents = useMemo(() => {
    let filtered = events;
    if (senderFilter) {
      const lower = senderFilter.toLowerCase();
      filtered = filtered.filter(
        (ev) =>
          ev.sender_email.toLowerCase().includes(lower) ||
          (ev.sender_display_name ?? "").toLowerCase().includes(lower),
      );
    }
    if (dateFrom) {
      const from = new Date(dateFrom).getTime();
      filtered = filtered.filter(
        (ev) => ev.sent_at && new Date(ev.sent_at).getTime() >= from,
      );
    }
    if (dateTo) {
      const to = new Date(dateTo).getTime() + 86400000; // end of day
      filtered = filtered.filter(
        (ev) => ev.sent_at && new Date(ev.sent_at).getTime() < to,
      );
    }
    return filtered;
  }, [events, senderFilter, dateFrom, dateTo]);

  // Selected preview
  const selectedEvents = events.filter((ev) => selected.has(ev.message_id));

  return (
    <div className="space-y-2">
      {/* Filters */}
      <div className="flex flex-wrap gap-2 text-xs">
        <input
          type="text"
          placeholder="Filter by sender..."
          value={senderFilter}
          onChange={(e) => setSenderFilter(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1"
          data-testid="sender-filter"
        />
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1"
          data-testid="date-from-filter"
        />
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1"
          data-testid="date-to-filter"
        />
      </div>

      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{INGESTION_COPY.emailListHeading}</h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400" data-testid="selection-count">
            {selected.size} selected
          </span>
          <button
            onClick={onSelectAll}
            className="text-xs text-blue-600 hover:underline"
          >
            {selected.size === events.length
              ? INGESTION_COPY.deselectAll
              : INGESTION_COPY.selectAll}
          </button>
        </div>
      </div>

      <div
        className="max-h-96 overflow-y-auto rounded border"
        data-testid="curation-email-scroll"
      >
        {filteredEvents.map((ev) => (
          <label
            key={ev.event_id}
            className={`flex cursor-pointer items-start gap-3 border-b p-3 text-sm hover:bg-gray-50 ${
              selected.has(ev.message_id) ? "bg-blue-50" : ""
            }`}
          >
            <input
              type="checkbox"
              checked={selected.has(ev.message_id)}
              onChange={() => onToggle(ev.message_id)}
              className="mt-1"
            />
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{ev.subject}</p>
              <p className="text-xs text-gray-500">
                {ev.sender_display_name ?? ev.sender_email}
                {ev.sent_at && (
                  <> &middot; {new Date(ev.sent_at).toLocaleDateString()}</>
                )}
              </p>
            </div>
          </label>
        ))}
      </div>

      {/* Diversity metrics */}
      {diversityMetrics && (
        <div className="mt-2 rounded border border-slate-200 p-2 text-xs text-slate-600" data-testid="diversity-metrics">
          <span className="font-medium">Coverage: </span>
          <span data-testid="diversity-sender-band">
            {SENDER_BAND_LABELS[diversityMetrics.sender_band] ??
              diversityMetrics.sender_band}
          </span>
          <span className="mx-1">&middot;</span>
          <span>Date range: {diversityMetrics.date_range_band}</span>
        </div>
      )}

      {/* Selection preview pane */}
      {selectedEvents.length > 0 && (
        <div className="mt-2" data-testid="selection-preview">
          <h4 className="text-xs font-medium text-slate-500">
            Preview ({selectedEvents.length})
          </h4>
          <ul className="mt-1 max-h-32 overflow-y-auto text-xs text-slate-600">
            {selectedEvents.slice(0, 10).map((ev) => (
              <li key={ev.event_id} className="truncate">
                {ev.subject} — {ev.sender_email}
              </li>
            ))}
            {selectedEvents.length > 10 && (
              <li className="text-slate-400">
                ...and {selectedEvents.length - 10} more
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
