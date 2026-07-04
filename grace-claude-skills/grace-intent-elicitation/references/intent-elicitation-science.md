# Intent-elicitation science — the validated question method

Grounded in GrACE's established elicitation science (`grace-review-protocol/references/
review-protocol-science.md`, Chunks 27–30/44) and validated LIVE over 6 real decisions in
this build. This is the method; do not invent another.

## 1. The adaptation (fact-review science → intent extraction)
The documented science was built for FACT review (is this claim right?). Its *principles*
transfer to intent extraction verbatim; its *capture schema* had to grow (the typed intent
layer). What carries over, and how:

| Documented mechanism | Original use | Carried into intent as |
|---|---|---|
| **Teach-Back anti-anchoring** (D226, Buçinca 2021) | Human labels evidence sentences before dispositioning, so they engage evidence not the AI | The *principle* — evidence-first, **propose nothing**. The "correction" the human writes becomes **the rationale they supply**. |
| **Laddering** (decomposition probing) | "What makes this a Milestone?" surfaces tacit criteria | **Surgical** laddering — probe the ONE rung an expert left implicit (often the counterfactual or the leverage) |
| **Certainty bands** (D120/D217) | high/med/low/insufficient on a decision | Same bands on each rationale + a **resolver** (what would raise it) + rival hypothesis |
| **Plain language** (D522) | No ontology jargon in review | Same — talk decisions/terms/tradeoffs |
| **Five-phase lifecycle** | prepare→open→structure→clarify→close | Reused; `structure` is the elicitation, `confirm` is the human gate |

## 2. The one rule that is everything: anti-anchoring
Reviewers mirror a proposed answer **60–80%** of the time (Buçinca, Malaya & Gajos 2021, CHI).
So if you propose the "why", you don't extract intent — you manufacture a confabulated echo.

- **WRONG:** "Aimmune bears all the regulatory cost — was that to protect Xencor's downside?"
  → the human says "yes" and the rationale is yours, not theirs.
- **RIGHT:** present the verbatim fact, then "Why is it built that way?" → the human reasons
  from the evidence and gives you *their* mental model.

Validated: across 6 questions with zero proposed hypotheses, every answer was an original
mental model ("control follows value; risk follows control"; "who is best positioned to manage
the shipment"; "alignment beats appearance fees"). No mirroring.

## 3. Surgical laddering
An expert self-ladders to root unprompted. Your job is not to ask "why" five times — it is to
spot the ONE rung they left implicit and probe exactly that. The implicit rung is usually:
- the **counterfactual** (what would the rejected path have cost?) — Q1 ladder surfaced the
  150–200 bps royalty step-up that was rejected;
- the **leverage / provenance** (was it contested or conceded?) — Q2 ladder surfaced "mild
  fight, folded in a week"; Q6 ladder surfaced the `traded_for` pair;
- the **decisive lever** when motive is mixed — Q3 ladder resolved "Nantz's ask, Ashworth's
  structure", moving the band from medium → high.

## 4. From answer → typed structure
After the human reasons (and ladders), distill *with them* into:
- **principle(s)** — the reusable rule. Check `intent_query.py --similar`; reuse an existing
  principle if one fits (a principle that spans documents is the whole point). New rule → new
  principle.
- **rationale** — `summary` + `constraint` (what forced it) + `stakes` (what breaks if reversed)
  + `leverage` (the power asymmetry) + `negotiation` (who argued what, what was conceded).
- **counterfactual(s)** — the rejected path(s); `demanded` + `why_rejected`. Fenced.
- **traded_for** — when a concession was paired with what it bought (two real facts).
- **certainty band** + **resolver** on the rationale.

This is a *judgment* step — so it goes to the human-confirm gate (skill step 4) before any write.

## 5. The 6-decision validation corpus (what each taught)
| Decision | Principle(s) captured | Taught |
|---|---|---|
| Xencor/Aimmune (risk allocation) | control_follows_value, risk_follows_control | the typed structure; one rationale → 3 facts |
| Apollo (Ex Works) | risk_follows_control (REUSED), smaller_party_caps_exposure_early | cross-document principle reuse |
| Ashworth (board seat) | align_economics, structural_substitute, deliver_via_cheapest_instrument | a band resolved UP via laddering; multi-principle decision |
| Allison/ValueAct (standstill) | structural_substitute (REUSED cross-domain), neutralize_a_threat, tie_concession_to_stake | reuse across unrelated domains (golf→activism) |
| Watson (clawback) | use_loss_aversion_to_self_enforce | a behavioral principle; the formula as mechanism |
| Accuray (governing-law trade) | never_concede_for_free | the `traded_for` construct; a band honestly LEFT at medium with a resolver |

## 6. The extraction rule (proven on a cold scenario)
A never-seen automotive-battery deal was resolved by intent captured from pharma/golf/
transmissions. The correct principle was reliably retrieved — but often at **rank #2**, because
a semantic attractor (`smaller_party_caps_exposure_early`) over-ranks on small-party scenarios.
**Rule: retrieve top-K, select using `applies_when`, never trust rank #1.** Embed principles over
`statement + applies_when` (statement alone ranked the right principle #3 → #1 once scope was added).

## 7. Honesty discipline
- If the human doesn't actually know the original intent (these may be third-party documents),
  the band is `insufficient_evidence` or `low` — and that gap is itself durable context. Never
  fill it with a plausible guess; that would poison the graph with confabulated intent.
- A rejected alternative is NEVER described downstream as if it happened. The fence is enforced
  in code (`is_term=false`) and in how you narrate.
