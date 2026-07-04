"use client";

/**
 * /communications/profiles/[person_id] — Profile detail (Chunk 60, CP5).
 *
 * StyleSignature band cards + per-recipient StyleDelta shift chips.
 * Neutral palette for shifts (never red/green — EC-12).
 * Low-confidence recipients muted with provisional advisory.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  fetchProfile,
  fetchCategoryProfile,
  type ProfileDetail,
  type CategoryProfile,
} from "@/lib/api/communications";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { buildEnvelope } from "@/lib/telemetry/events";
import { useSessionStore } from "@/lib/state/session-store";

const STYLE_SIG_FIELDS = [
  "sentence_length_band",
  "vocabulary_complexity_band",
  "formality_band",
  "greeting_closing_band",
  "hedging_frequency_band",
  "directness_band",
  "response_timing_band",
  "thread_depth_band",
] as const;

const SHIFT_FIELDS = [
  "sentence_length_shift",
  "vocabulary_complexity_shift",
  "formality_shift",
  "hedging_shift",
  "directness_shift",
  "response_timing_shift",
] as const;

const OVERRIDE_FIELDS = ["greeting_override", "closing_override"] as const;

const D422_CATEGORIES = [
  "executive_superior",
  "direct_manager",
  "peer_same_department",
  "peer_cross_department",
  "direct_report",
  "external_vendor",
  "external_client",
  "legal_counsel",
  "new_hire_onboarding",
  "general_distribution",
] as const;

const COLLAPSE_THRESHOLD = 50;

function humanLabel(field: string): string {
  return field
    .replace(/_band$/, "")
    .replace(/_shift$/, "")
    .replace(/_override$/, "")
    .replace(/_/g, " ");
}

export default function ProfileDetailPage() {
  const params = useParams();
  const personId = params.person_id as string;
  const sessionId = useSessionStore((s) => s.sessionId);
  const telemetrySent = useRef(false);

  const [profile, setProfile] = useState<ProfileDetail | null>(null);
  const [categories, setCategories] = useState<
    Map<string, CategoryProfile>
  >(new Map());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);

  const loadProfile = useCallback(async () => {
    try {
      const p = await fetchProfile(personId);
      setProfile(p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }, [personId]);

  const loadCategories = useCallback(async () => {
    for (const cat of D422_CATEGORIES) {
      try {
        const catProfile = await fetchCategoryProfile(personId, cat);
        setCategories((prev) => new Map(prev).set(cat, catProfile));
      } catch {
        // Category may not exist — skip silently
      }
    }
  }, [personId]);

  useEffect(() => {
    void loadProfile();
    void loadCategories();
  }, [loadProfile, loadCategories]);

  useEffect(() => {
    if (telemetrySent.current || !sessionId) return;
    telemetrySent.current = true;
    void postElicitationEvent(
      buildEnvelope({
        session_id: sessionId,
        phase_name: "none",
        event_type: "profile_detail_viewed",
        payload: { person_id: personId },
      }),
    );
  }, [personId, sessionId]);

  if (err) {
    return (
      <div className="p-4">
        <p className="text-red-700">{err}</p>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="p-4">
        <p className="text-slate-400">Loading...</p>
      </div>
    );
  }

  const sig = profile.style_signature ?? {};

  return (
    <div className="p-4" data-testid="profile-detail-page">
      <Link
        href="/communications/profiles"
        className="mb-2 inline-block text-xs text-blue-600 underline"
      >
        &larr; Profiles
      </Link>

      <h1 className="mb-1 text-lg font-semibold">
        Profile: {profile.person_id}
      </h1>
      <p className="mb-4 text-sm text-slate-500">
        Version {profile.profile_version} &mdash; Quality:{" "}
        {profile.profile_quality_band}
      </p>

      {/* StyleSignature band cards */}
      <section className="mb-6">
        <h2 className="mb-2 text-sm font-medium text-slate-600">
          Style signature
        </h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="band-cards">
          {STYLE_SIG_FIELDS.map((field) => (
            <div
              key={field}
              className="rounded border border-slate-200 px-3 py-2"
              data-testid="band-card"
            >
              <p className="text-xs text-slate-500">{humanLabel(field)}</p>
              <p className="text-sm font-medium">{sig[field] ?? "—"}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Per-category recipients */}
      <section>
        <h2 className="mb-2 text-sm font-medium text-slate-600">
          Recipients by category
        </h2>
        {D422_CATEGORIES.map((cat) => {
          const catData = categories.get(cat);
          if (!catData || catData.recipients.length === 0) return null;

          const isCollapsed = collapsed.has(cat);
          const shouldCollapse =
            catData.recipients.length > COLLAPSE_THRESHOLD;
          const visibleRecipients =
            shouldCollapse && isCollapsed
              ? catData.recipients.slice(0, 10)
              : catData.recipients;

          return (
            <div
              key={cat}
              className="mb-3"
              data-testid="category-group"
            >
              <button
                type="button"
                className="mb-1 text-sm font-medium text-slate-700"
                onClick={() => {
                  setCollapsed((prev) => {
                    const next = new Set(prev);
                    if (next.has(cat)) next.delete(cat);
                    else next.add(cat);
                    return next;
                  });
                }}
                data-testid="category-toggle"
              >
                {cat.replace(/_/g, " ")} ({catData.recipients.length})
                {shouldCollapse && (
                  <span className="ml-1 text-xs text-slate-400">
                    {isCollapsed ? "[expand]" : "[collapse]"}
                  </span>
                )}
              </button>

              <div className="space-y-1">
                {visibleRecipients.map((r) => {
                  const isLow = r.confidence_band === "low";
                  const delta = r.style_delta ?? {};

                  return (
                    <div
                      key={r.recipient_person_id}
                      className={`rounded border border-slate-100 px-3 py-1 text-xs ${
                        isLow ? "opacity-50" : ""
                      }`}
                      data-testid="recipient-row"
                    >
                      <span className="font-medium">
                        {r.recipient_person_id}
                      </span>
                      {isLow && (
                        <span
                          className="ml-2 text-slate-400"
                          data-testid="low-confidence-advisory"
                        >
                          (provisional — limited data)
                        </span>
                      )}

                      {/* Shift chips — neutral palette (EC-12: never red/green) */}
                      <div
                        className="mt-1 flex flex-wrap gap-1"
                        data-testid="shift-chips"
                      >
                        {SHIFT_FIELDS.map((sf) => {
                          const val = delta[sf];
                          if (!val) return null;
                          return (
                            <span
                              key={sf}
                              className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-700"
                              data-testid="shift-chip"
                            >
                              {humanLabel(sf)}: {val}
                            </span>
                          );
                        })}
                        {OVERRIDE_FIELDS.map((of_) => {
                          const val = delta[of_];
                          if (!val) return null;
                          return (
                            <span
                              key={of_}
                              className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-700"
                              data-testid="override-chip"
                            >
                              {humanLabel(of_)}: {val}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </section>
    </div>
  );
}
