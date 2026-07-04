"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { proposalsApi, type ProposalItem, type DecideRequest } from "@/lib/api/proposals";
import { PROPOSALS_COPY } from "@/lib/proposals/copy";
import { useSessionStore } from "@/lib/state/session-store";
import { EventFactory } from "@/lib/telemetry/events";
import { postElicitationEvent } from "@/lib/telemetry/emit";

/**
 * D120/D217: raw_confidence band — never expose the number.
 */
function confidenceBand(raw: number): string {
  if (raw >= 0.7) return PROPOSALS_COPY.confidenceBandHigh;
  if (raw >= 0.4) return PROPOSALS_COPY.confidenceBandMedium;
  return PROPOSALS_COPY.confidenceBandLow;
}

export default function ProposalDetailPage() {
  const params = useParams();
  const router = useRouter();
  const proposalId = params.proposal_id as string;
  const sessionId = useSessionStore((s) => s.sessionId);

  const [proposal, setProposal] = useState<ProposalItem | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [reviewer, setReviewer] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [decideErr, setDecideErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const p = await proposalsApi.get(proposalId);
        if (!cancelled) {
          setProposal(p);
          setErr(null);
          // Emit proposal_viewed telemetry.
          if (sessionId) {
            const evt = EventFactory.proposalViewed(sessionId, {
              proposal_id: p.id,
              change_tier: p.change_tier,
            });
            postElicitationEvent(evt).catch(() => {});
          }
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : "Load failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [proposalId, sessionId]);

  async function handleDecide(decision: DecideRequest["decision"]) {
    if (!reviewer.trim()) return;
    setDeciding(true);
    setDecideErr(null);
    try {
      const updated = await proposalsApi.decide(proposalId, {
        decision,
        reviewer: reviewer.trim(),
      });
      setProposal(updated);
    } catch (e) {
      setDecideErr(e instanceof Error ? e.message : "Decision failed");
    } finally {
      setDeciding(false);
    }
  }

  if (loading) return <div className="p-4 text-sm text-slate-500">Loading…</div>;
  if (err || !proposal)
    return (
      <div className="p-4">
        <p className="text-sm text-red-600">{err ?? PROPOSALS_COPY.detailNotFound}</p>
      </div>
    );

  return (
    <main
      data-testid="proposal-detail-page"
      className="mx-auto flex max-w-3xl flex-col gap-4 p-4"
    >
      <header>
        <button
          className="mb-2 text-xs text-blue-600 hover:underline"
          onClick={() => router.push("/proposals")}
        >
          &larr; All proposals
        </button>
        <h1 className="text-lg font-semibold">{proposal.kgcl_command}</h1>
        <p className="text-xs text-slate-500">
          Tier {proposal.change_tier} &middot;{" "}
          {confidenceBand(proposal.raw_confidence)} &middot; {proposal.status}
        </p>
      </header>

      {proposal.overflow ? (
        <div
          className="rounded border border-yellow-300 bg-yellow-50 p-2 text-xs text-yellow-800"
          data-testid="overflow-banner"
        >
          {PROPOSALS_COPY.overflowBanner}
        </div>
      ) : null}

      {/* KGCL command */}
      <section className="rounded border border-slate-200 bg-white p-3">
        <h2 className="mb-1 text-sm font-medium">{PROPOSALS_COPY.kgclHeading}</h2>
        <pre className="whitespace-pre-wrap text-xs text-slate-700">
          {proposal.kgcl_command}
        </pre>
      </section>

      {/* Evidence bundle */}
      <section
        className="rounded border border-slate-200 bg-white p-3"
        data-testid="evidence-bundle-panel"
      >
        <h2 className="mb-1 text-sm font-medium">
          {PROPOSALS_COPY.evidenceHeading}
        </h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <dt className="text-slate-500">{PROPOSALS_COPY.evidenceSignalType}</dt>
          <dd>{proposal.evidence.signal_type}</dd>
          <dt className="text-slate-500">{PROPOSALS_COPY.evidenceModule}</dt>
          <dd>{proposal.evidence.ontology_module}</dd>
          <dt className="text-slate-500">
            {PROPOSALS_COPY.evidenceAffectedTypes}
          </dt>
          <dd>{proposal.evidence.affected_entity_types.join(", ")}</dd>
        </dl>
        {proposal.evidence.evidence_summary_nl ? (
          <div className="mt-2">
            <h3 className="text-xs font-medium text-slate-500">
              {PROPOSALS_COPY.evidenceSummary}
            </h3>
            <p className="text-xs text-slate-700">
              {proposal.evidence.evidence_summary_nl}
            </p>
          </div>
        ) : (
          <p className="mt-2 text-xs text-slate-400">
            {PROPOSALS_COPY.evidenceNoSummary}
          </p>
        )}
        {proposal.evidence.example_text_snippets &&
        proposal.evidence.example_text_snippets.length > 0 ? (
          <div className="mt-2" data-testid="evidence-snippets">
            <h3 className="text-xs font-medium text-slate-500">
              {PROPOSALS_COPY.evidenceExampleSnippets}
            </h3>
            <ul className="mt-1 list-inside list-disc text-xs text-slate-700">
              {proposal.evidence.example_text_snippets.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {proposal.evidence.example_documents &&
        proposal.evidence.example_documents.length > 0 ? (
          <div className="mt-2" data-testid="evidence-documents">
            <h3 className="text-xs font-medium text-slate-500">
              {PROPOSALS_COPY.evidenceExampleDocuments}
            </h3>
            <ul className="mt-1 list-inside list-disc text-xs text-slate-700">
              {proposal.evidence.example_documents.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      {/* Decision bar — only for pending proposals */}
      {proposal.status === "pending" ? (
        <section
          className="rounded border border-slate-200 bg-white p-3"
          data-testid="proposal-decision-bar"
        >
          <h2 className="mb-2 text-sm font-medium">
            {PROPOSALS_COPY.decisionHeading}
          </h2>
          <div className="mb-2">
            <label className="text-xs text-slate-500">
              {PROPOSALS_COPY.decisionReviewer}
              <input
                className="ml-2 rounded border px-2 py-0.5 text-xs"
                placeholder={PROPOSALS_COPY.decisionReviewerPlaceholder}
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
              />
            </label>
          </div>
          <div className="flex gap-2">
            <button
              className="rounded bg-green-600 px-3 py-1 text-xs text-white disabled:opacity-50"
              disabled={deciding || !reviewer.trim()}
              onClick={() => handleDecide("approved")}
            >
              {PROPOSALS_COPY.decisionApprove}
            </button>
            <button
              className="rounded bg-red-600 px-3 py-1 text-xs text-white disabled:opacity-50"
              disabled={deciding || !reviewer.trim()}
              onClick={() => handleDecide("rejected")}
            >
              {PROPOSALS_COPY.decisionReject}
            </button>
            <button
              className="rounded bg-blue-600 px-3 py-1 text-xs text-white disabled:opacity-50"
              disabled={deciding || !reviewer.trim()}
              onClick={() => handleDecide("modified")}
            >
              {PROPOSALS_COPY.decisionModify}
            </button>
            <button
              className="rounded border border-slate-400 bg-white px-3 py-1 text-xs text-slate-800 disabled:opacity-50"
              disabled={deciding || !reviewer.trim()}
              onClick={() => handleDecide("deferred")}
            >
              {PROPOSALS_COPY.decisionDefer}
            </button>
          </div>
          {decideErr ? (
            <p className="mt-1 text-xs text-red-600">{decideErr}</p>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}
