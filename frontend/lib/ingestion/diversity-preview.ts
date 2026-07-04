/** Client-side diversity band preview — mirrors `ingestion_routes.curate_emails` (D432). */

export interface EventForDiversity {
  sender_email: string;
  sent_at: string | null;
  thread_id?: string | null;
}

export interface DiversityBands {
  sender_band: string;
  thread_depth_band: string;
  date_range_band: string;
}

export function computeDiversityPreview(
  events: EventForDiversity[],
): DiversityBands {
  if (events.length === 0) {
    return {
      sender_band: "narrow",
      thread_depth_band: "mostly_single",
      date_range_band: "short",
    };
  }

  const senderCount = new Set(events.map((e) => e.sender_email)).size;
  let sender_band: string;
  if (senderCount < 5) {
    sender_band = "narrow";
  } else if (senderCount <= 20) {
    sender_band = "balanced";
  } else {
    sender_band = "wide";
  }

  const threadGroups: Record<string, number> = {};
  for (const ev of events) {
    const key = ev.thread_id ?? "__null__";
    threadGroups[key] = (threadGroups[key] ?? 0) + 1;
  }
  const meanDepth =
    Object.values(threadGroups).reduce((a, b) => a + b, 0) /
    Math.max(Object.keys(threadGroups).length, 1);
  let thread_depth_band: string;
  if (meanDepth <= 1.5) {
    thread_depth_band = "mostly_single";
  } else if (meanDepth <= 3.0) {
    thread_depth_band = "mixed";
  } else {
    thread_depth_band = "deep_threaded";
  }

  const sentDates = events
    .map((e) => (e.sent_at ? new Date(e.sent_at).getTime() : null))
    .filter((t): t is number => t !== null);
  let spanDays = 0;
  if (sentDates.length >= 2) {
    spanDays = Math.floor(
      (Math.max(...sentDates) - Math.min(...sentDates)) / 86_400_000,
    );
  }
  let date_range_band: string;
  if (spanDays < 30) {
    date_range_band = "short";
  } else if (spanDays <= 365) {
    date_range_band = "quarter";
  } else {
    date_range_band = "year_plus";
  }

  return { sender_band, thread_depth_band, date_range_band };
}
