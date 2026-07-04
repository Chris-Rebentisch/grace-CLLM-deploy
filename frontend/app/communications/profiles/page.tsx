"use client";

/**
 * /communications/profiles — Profile browser list (Chunk 60, CP5).
 *
 * Searchable person list consuming GET /api/communications/profiles.
 * Emits profile_browser_viewed telemetry on mount.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  fetchProfiles,
  type ProfileListItem,
} from "@/lib/api/communications";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { buildEnvelope } from "@/lib/telemetry/events";
import { useSessionStore } from "@/lib/state/session-store";

export default function ProfileListPage() {
  const [profiles, setProfiles] = useState<ProfileListItem[]>([]);
  const [search, setSearch] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const sessionId = useSessionStore((s) => s.sessionId);
  const telemetrySent = useRef(false);

  const load = useCallback(async () => {
    try {
      const res = await fetchProfiles(undefined, 100);
      setProfiles(res.items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (telemetrySent.current || !sessionId) return;
    telemetrySent.current = true;
    void postElicitationEvent(
      buildEnvelope({
        session_id: sessionId,
        phase_name: "none",
        event_type: "profile_browser_viewed",
        payload: { profiles_visible_count: profiles.length },
      }),
    );
  }, [profiles, sessionId]);

  const filtered = search
    ? profiles.filter((p) =>
        p.person_id.toLowerCase().includes(search.toLowerCase()),
      )
    : profiles;

  if (err) {
    return (
      <div className="p-4">
        <p className="text-red-700">{err}</p>
      </div>
    );
  }

  return (
    <div className="p-4" data-testid="profile-list-page">
      <h1 className="mb-3 text-lg font-semibold">Communication profiles</h1>

      <input
        type="text"
        placeholder="Search by person ID..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mb-4 w-full rounded border border-slate-300 px-3 py-1 text-sm"
        data-testid="profile-search"
      />

      {filtered.length === 0 ? (
        <p className="text-sm text-slate-400" data-testid="profile-list-empty">
          No profiles found.
        </p>
      ) : (
        <ul className="space-y-1">
          {filtered.map((p) => (
            <li
              key={p.person_id}
              className="flex items-center justify-between rounded border border-slate-200 px-3 py-2 text-sm"
              data-testid="profile-row"
            >
              <Link
                href={`/communications/profiles/${p.person_id}`}
                className="text-blue-600 underline"
              >
                {p.person_id}
              </Link>
              <span className="text-xs text-slate-400">
                v{p.profile_version} &mdash; {p.profile_quality_band}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4">
        <Link
          href="/communications/profiles/aggregate/all"
          className="text-xs text-blue-600 underline"
        >
          View aggregate profiles
        </Link>
      </div>
    </div>
  );
}
