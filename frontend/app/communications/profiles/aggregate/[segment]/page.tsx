"use client";

/**
 * /communications/profiles/aggregate/[segment] — Aggregate profile (Chunk 60, CP5).
 *
 * Reads from department_communication_profiles VIEW via
 * GET /api/communications/profiles/aggregate/{segment}.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  fetchAggregateProfile,
  type AggregateProfile,
} from "@/lib/api/communications";

export default function AggregateProfilePage() {
  const params = useParams();
  const segment = params.segment as string;

  const [profile, setProfile] = useState<AggregateProfile | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const p = await fetchAggregateProfile(segment);
      setProfile(p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }, [segment]);

  useEffect(() => {
    void load();
  }, [load]);

  if (err) {
    return (
      <div className="p-4">
        <p className="text-red-700" data-testid="aggregate-error">{err}</p>
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

  return (
    <div className="p-4" data-testid="aggregate-profile-page">
      <Link
        href="/communications/profiles"
        className="mb-2 inline-block text-xs text-blue-600 underline"
      >
        &larr; Profiles
      </Link>

      <h1 className="mb-3 text-lg font-semibold">
        Aggregate: {profile.aggregate_segment}
      </h1>
      <p className="mb-2 text-sm text-slate-500">
        {profile.profile_count} profiles in this segment
      </p>

      <div className="grid grid-cols-3 gap-2" data-testid="aggregate-bands">
        <div className="rounded border border-slate-200 px-3 py-2">
          <p className="text-xs text-slate-500">Sentence length</p>
          <p className="text-sm font-medium">
            {profile.avg_sentence_length_band}
          </p>
        </div>
        <div className="rounded border border-slate-200 px-3 py-2">
          <p className="text-xs text-slate-500">Formality</p>
          <p className="text-sm font-medium">
            {profile.avg_formality_band}
          </p>
        </div>
        <div className="rounded border border-slate-200 px-3 py-2">
          <p className="text-xs text-slate-500">Directness</p>
          <p className="text-sm font-medium">
            {profile.avg_directness_band}
          </p>
        </div>
      </div>
    </div>
  );
}
