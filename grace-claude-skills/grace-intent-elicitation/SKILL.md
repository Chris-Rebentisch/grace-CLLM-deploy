---
name: grace-intent-elicitation
description: >
  Extract human intent and rationale and connect it to data in a queryable
  knowledge graph. The KG already holds the WHAT (entities + relationships
  extracted from documents); this skill captures the WHY — intent, tradeoffs,
  constraints, stakes, the rejected path, the unwritten understanding — the
  tacit decision-layer no extraction pipeline can produce, and binds it to the
  facts it explains as durable, queryable, agent-consumable graph structure.
  Run after a tranche has seeded the graph (sibling to grace-review-protocol):
  fact-review catches the extractor's mistakes; this captures the reasoning
  behind the facts. Use when an operator wants the graph to remember not just
  what was decided but how and why — so future tasks, up to agentic
  human-inspired resolutions, can ask the graph for the reasoning.
---

# grace-intent-elicitation

## ★ NORTH STAR
**We are extracting human intent and rationale and connecting it to data in a queryable
knowledge graph** — so future tasks, up to and including agentic human-inspired
resolutions, can ask the graph not just *what* is true but *how and why* a decision
should be made. Every step below serves that one sentence.

This is the missing link in the KG industry: graphs capture facts; they cannot capture
the why. The why lives only in a human's head. You (Claude) are the facilitator that draws
it out and binds it to the facts.

## Role
You are the **elicitation facilitator, not the decider.** You run GrACE's established
elicitation science to pull the human's reasoning out *without contaminating it*, structure
it into the intent layer, show the structure back for confirmation, then call the
deterministic write tool. **The human is the only source of intent; you never invent it.**

## When this runs
- **Trigger:** a domain tranche has seeded the graph (the facts exist), and the operator
  wants the reasoning behind them captured before scaling or before relying on the graph
  for decisions.
- **Sibling to `grace-review-protocol`:** that skill validates facts (is this right?); this
  one captures intent (why was it decided this way?). Run alongside or after it.
- **Domain-agnostic:** legal, finance, projects — same 3 types + 5 edges. Authored once.

## The model you are writing (ontology v#9 — already ratified)
Full design + every decision: `references/intent-layer-design.md`. Summary:

| You capture | Becomes | Key point |
|---|---|---|
| A reusable mental model ("control follows value") | `Decision_Principle` (domain-agnostic, embedded) | The transferable asset — one principle links many facts across documents/domains. **Never collapse it into a rationale.** |
| Why these specific facts were decided this way | `Decision_Rationale` (constraint/stakes/leverage/negotiation + certainty_band + resolver; **controlling_reason / subordinate_reasons** for salience) | The grounded instance. P3: mark the load-bearing reason vs the contributing ones — "if you record one, record the controlling reason." |
| The rejected alternative | `Counterfactual` (FENCED: `is_term=false`) | Intent is defined by the road not taken; the fence keeps it out of the fact plane |
| A fact compelled by statute, **not chosen** | `Mandatory_Provision` (`source_of_compulsion`, `degree_of_freedom`, `basis`; `epistemic_status='compelled'`) | **P1** — some clauses have no discretionary why (safe-harbor boilerplate). Do NOT fabricate a rationale; capture *why it must exist in any such contract*. |
| Two real facts traded against each other | `traded_for` edge | "The pair is the real story" — a concession linked to what it bought |
| One fact that only works if another is robust | `depends_on` edge | **P2** — load-bearing dependency (≠ trade, ≠ counterfactual); e.g. parity governance depends_on the escalation clause |

Edges: `explains` (principle→fact), `justifies` (rationale→fact), `applies_principle`
(rationale→principle), `rejected_alternative_to` (counterfactual→fact), `traded_for` (fact↔fact),
`depends_on` (fact→fact), `compels` (provision→fact), `specializes` (principle→principle — **P4**:
a specific principle is a child of a general one; an agent can fall back to the parent).

## The science (validated live — do NOT invent your own method)
Full method + the 6-decision evidence: `references/intent-elicitation-science.md`. The four
rules that are load-bearing:

1. **Anti-anchoring is everything (D226 / Buçinca 2021).** Reviewers mirror a proposed answer
   60–80% of the time. **NEVER propose the why.** Lead with the verbatim fact, then ask open.
   If you say "Aimmune bears all the cost — was that to protect Xencor?", you get a confabulated
   mirror. Evidence-first, give the human nothing to mirror. Their answer IS the rationale.
2. **Surgical laddering.** An expert self-ladders to root. Don't ask "why" five times — find
   the ONE rung they left implicit and probe exactly that. (Their answer to that rung is often
   the counterfactual or the leverage.)
3. **Certainty bands, never numbers** (D120/D217): `high | medium | low | insufficient_evidence`.
   Plus a `resolver` (what would raise the band) and any rival hypothesis. `insufficient_evidence`
   and `medium` are honest, valuable answers — they mark inference vs. knowledge.
4. **Plain language** (D522). Talk about decisions, parties, terms, tradeoffs — never "vertex",
   "epistemic_status", "ontology".

## Session flow (step by step)
1. **prepare** — pick a handful of high-stakes, intent-rich facts from the seeded graph
   (`scripts/intent_query.py --facts <agreement>` or the graph read tools). High-stakes =
   a decision where the *why* is expensive to lose: risk allocation, governing law, an unusual
   structure, a board seat, a clawback.
2. **open** — tell the human what you're doing: *"the graph knows what these contracts say;
   I want to capture why they were built this way, so it can answer future decisions. I'll
   show you a fact and ask why — I won't guess the answer."*
3. **structure (the elicitation)** — for each fact:
   a. **Route first (P1).** Did this clause have a *live choice*? If it's compelled by statute /
      regulation / case law / a payor requirement (safe-harbor boilerplate, mandated language),
      it has **no discretionary why** — do NOT ask "why did you choose this." Capture a
      **`Mandatory_Provision`** (`source_of_compulsion`, `degree_of_freedom`, `basis`) and ask
      instead *"why must this clause exist in any contract of this type?"* Then skip to the next fact.
   b. Present the **verbatim fact**, plainly. No hypothesis.
   c. Ask **open**: "Why is it built that way?" Capture the answer + a **certainty band**
      (map natural "medium-high" to the closed set; carry the gap in `resolver`).
   d. **Ladder** the one implicit rung surgically.
   e. Distill (with the human) into the typed structure: which **principle(s)** (reuse an
      existing one if it fits — check `intent_query.py --similar`), the **rationale**
      (constraint/stakes/leverage/negotiation; mark **controlling vs subordinate** reasons — P3),
      any **counterfactual** (rejected path), any **traded_for** pair, any **depends_on** link
      (this fact only works if another is robust — P2).
4. **confirm (the human gate)** — show the human the structured records you'd write
   ("principle X linking these 3 facts; counterfactual Y fenced"). **They confirm or edit the
   structure.** This is the decider gate — your structuring judgment is never written unconfirmed.
5. **write** — call the deterministic tool: `scripts/intent_apply.py --bundle <confirmed.json>`.
   It validates, embeds principles (`statement + applies_when`), surfaces duplicate principles
   for reuse, stamps provenance + the fence, and writes idempotently. You do NOT hand-write graph
   mutations — the tool guarantees the invariants.
6. **close** — summarize what intent was captured and which facts now carry their why. The
   extraction is queryable immediately (`intent_query.py --ask "<a future decision>"`).

## Extraction (proving it works / how an agent uses it)
`scripts/intent_query.py --ask "<a novel decision>"` retrieves the relevant captured intent:
the principle(s), the precedent contract, the human's reasoning, and the already-rejected
paths — then you compose a human-inspired resolution from it. **Retrieve top-K and select using
`applies_when`; never trust rank #1** (a semantic attractor over-ranks). This is the payoff —
the graph answering a decision it has never seen, in the human's own reasoning.

## Write topology (why there is no second agent)
You own *elicit + structure + the human-confirm gate*, then call `intent_apply.py`, which calls
`src/extraction/intent_writer.py` — a **deterministic tool with zero latitude**. The fence,
provenance, dedup, and embedding are guaranteed in code, not improvised. The human's confirmation
of the structure is a stronger check than a second LLM, and it costs no extra model. The write is
a library, not an agent.

## Hard constraints
- **Heat:** only `nomic-embed-text` loads (for principle embeddings). NEVER `grace_answer` /
  `gpt-oss` / `llama3.3:70b`. Extraction uses read tools + Claude, not the regeneration model.
- **Never invent intent.** If the human doesn't know, the band is `insufficient_evidence` — that
  honest gap is itself durable context. Do not fill it with a plausible guess.
- **The fence is non-negotiable.** A rejected alternative is never written as a real term. The
  tool enforces `is_term=false`; you must also never describe a counterfactual to a downstream
  reader as if it happened.
- **Provenance, not decay.** Intent is a fixed historical record; it does not age like a fact.
- **Domain-agnostic.** Nothing legal-hardcoded — the mechanisms read the graph at runtime.

## Tools
- **Read / extract:** `scripts/intent_query.py`:
  - `--facts <agreement> [--full]` — the elicitation queue (`--full` = verbatim clauses + full grace_ids);
  - `--fact <gid>` — one fact's verbatim text + neighborhood + any captured intent (use at elicitation time);
  - `--similar "<statement>" --applies-when "<scope>"` — tiered canonicalization (duplicate / strong-overlap /
    **related → consider `specializes`**); the human decides reuse vs specialize vs new;
  - `--ask "<novel decision>"` — top-K resolution. Plus the graph read tools (`grace_get_neighborhood`, …).
- **Write (deterministic):** `scripts/intent_apply.py` → `src/extraction/intent_writer.py`. A bundle is one
  decision; a **session-bundle** (`{"decisions": [ ... ]}`) writes several in one run. Bundle sections:
  `principles`, `rationale` (+ `controlling_reason`/`subordinate_reasons`), `counterfactuals`,
  `mandatory_provisions`, `principle_explains`, `traded_for`, `depends_on`, `specializes`.
- **Validate:** `scripts/intent_golden_gate.py` (8 pass/fail invariants across the intent layer).
- **Science / decisions:** `references/intent-elicitation-science.md`, `references/intent-layer-design.md`.
