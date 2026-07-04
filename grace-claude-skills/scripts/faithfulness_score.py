#!/usr/bin/env python3
"""Deterministic FAITHFULNESS scorer for regeneration answers — the A2 deliverable.

Faithfulness = does the decompressed answer stay strictly within the grounded
context the model was given? An answer is UNFAITHFUL when it asserts an entity,
name, or number that is NOT present in the context (a hallucinated fact / drifted
number/name). This is the regeneration mirror of A1's grounding check ("every
returned grace_id must resolve"): here every SALIENT token the answer asserts must
resolve in the fact set = (context ∪ query).

Why salient tokens (names + numbers), not whole sentences: hallucinations invent
ENTITIES and NUMBERS, not verbs. A paraphrased relation is fine as long as the
entities/figures it names are grounded. So the scorer extracts:
  • Title-Case name phrases (multi-word, or a single ≥4-char non-stopword),
  • numbers / percentages / money,
  • ALL-CAPS acronyms,
and checks each against the normalized fact set.

Abstention is faithful by construction: a sentence that says "the context does not
…/insufficient/cannot determine/not specified" is tagged ABSTAIN — it is the
correct behaviour when retrieval under-delivers, and must NOT be scored as a
hallucination. (This separates regeneration FAITHFULNESS from answer COMPLETENESS,
which is a retrieval property — see --expect.)

Deterministic + domain-agnostic: no LLM, no hardcoded names, pure string analysis.

Scores:
  • faithfulness   = (grounded + abstain sentences) / total sentences   [the headline]
  • token grounding = grounded salient tokens / total salient tokens
  • completeness   = expected tokens present in answer / expected tokens  (only with --expect)

  python3 faithfulness_score.py --answer-file ans.txt --context-file ctx.txt --query "..."
  echo "<answer>" | python3 faithfulness_score.py --context-file ctx.txt --query "..."
  python3 faithfulness_score.py --answer-file a.txt --compose-json compose.json   # context+query from regen_compose --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Single Title-Case tokens that are ordinary words / sentence-initial noise — never
# treated as a name on their own (multi-word Title sequences are always kept).
_CAP_STOP = frozenset({
    "the", "this", "that", "these", "those", "it", "its", "a", "an", "in", "on",
    "of", "for", "to", "and", "or", "but", "no", "not", "none", "however",
    "therefore", "thus", "based", "according", "context", "however", "while",
    "additionally", "furthermore", "also", "there", "here", "they", "their",
    "as", "per", "see", "note", "i", "we", "you", "he", "she", "his", "her",
    "answer", "question", "provided", "given", "below", "above", "law", "laws",
    "agreement", "agreements", "obligation", "obligations", "entity", "entities",
    "is", "are", "was", "were", "be", "by", "with", "from", "at", "if",
    # quantity / ordinal words that often lead a sentence (not names)
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "first", "second", "third", "fourth", "fifth", "several", "many", "both",
    "all", "some", "each", "few", "multiple", "various", "only", "both",
    # H-1 (acceptance swarm): sentence-initial connectives / adverbs / common nouns
    # that are Title-Cased ONLY because they start a sentence — never real names.
    # Most-reported scorer false-positive class (close/narrative phase especially).
    "because", "although", "since", "when", "while", "where", "whereas", "though",
    "specifically", "notably", "importantly", "additionally", "finally", "overall",
    "consequently", "hence", "moreover", "furthermore", "deferred", "other", "others",
    "neither", "either", "instead", "rather", "similarly", "conversely", "meanwhile",
    "together", "overall", "summary", "summarizing", "regarding", "concerning",
    "under", "within", "without", "between", "through", "during", "per-",
    # F3 (SS-2): comprehensive sentence-opener set — "Among" slipped past H-1. These are
    # common English words capitalized only by sentence position, never entity names.
    "among", "amongst", "across", "after", "before", "beyond", "despite", "unless",
    "until", "upon", "about", "above", "below", "beside", "besides", "however",
    "thus", "therefore", "accordingly", "nonetheless", "nevertheless", "regardless",
    "indeed", "essentially", "ultimately", "primarily", "largely", "generally",
    "typically", "notwithstanding", "per", "via", "according", "based", "given",
    "such", "no", "yes", "here", "there", "then", "now", "today", "overall",
    "each", "every", "any", "most", "more", "less", "fewer", "much", "such",
    "this", "that", "these", "those", "their", "its", "his", "her", "our", "your",
    "they", "them", "it", "we", "he", "she", "you", "one", "both", "all", "none",
    "additionally", "alternatively", "crucially", "critically", "importantly",
    "interestingly", "remarkably", "presumably", "arguably", "clearly", "evidently",
    "first", "second", "third", "next", "lastly", "subsequently", "previously",
    "currently", "originally", "initially", "eventually", "meanwhile", "afterward",
    "if", "but", "and", "or", "nor", "so", "yet", "for", "to", "of", "in", "on",
    "at", "by", "as", "is", "are", "was", "were", "be", "being", "been",
})
# Sentence-level abstention / hedge markers — a sentence that hedges is FAITHFUL.
_ABSTAIN_CUES = (
    "does not", "do not", "not identif", "not specif", "not state", "not mention",
    "not contain", "not provide", "not include", "not available", "no agreement",
    "no information", "no mention", "insufficient", "cannot determine",
    "cannot answer", "can't answer", "unable to", "not enough", "is not clear",
    "not clear from", "not present", "not found", "doesn't", "don't", "isn't",
    "aren't", "lacks", "lack of", "absent", "unclear", "not explicitly",
    "context does", "based on the context", "the context only", "no explicit",
)

# R2 (session-4): negation / dismissal cues. A salient token that appears ONLY inside a
# negated or dismissive clause ("…but the context does not state…", "rather than X")
# is being DISMISSED, not asserted — so anti-expect must not fire on it, and a sentence
# that merely names distractors to reject them does not count as a positive answer.
_NEGATION_CUES = (
    " not ", "n't", " no ", "none", "neither", " nor ", "without", "rather than",
    "instead of", "cannot", "can not", "never", "but none", "does not", "do not",
    "did not", "is not", "are not", "was not", "were not",
)


def _sentence_negated(low: str) -> bool:
    """True if the (lowercased) sentence carries a negation/dismissal or abstention cue."""
    padded = f" {low.strip()} "
    return any(c in padded for c in _NEGATION_CUES) or any(c in padded for c in _ABSTAIN_CUES)


def _has_caps_or_digit(tok: str) -> bool:
    """True if a token is CamelCase (internal capital) or contains a digit — i.e. a real
    identifier, not a plain word capitalized only by sentence position (R1)."""
    return any(c.isdigit() for c in tok) or any(c.isupper() for c in tok[1:])


def _normalize(text: str) -> str:
    """Lowercase, collapse non-alphanumerics to single spaces (for substring tests)."""
    return re.sub(r"[^a-z0-9%$.]+", " ", text.lower())


# H-1 (acceptance swarm): markdown list markers ("1.", "2)", "-", "*", "•") at the
# start of a line are FORMATTING, not facts. Strip them before token extraction so
# an enumerated answer's ordinals aren't flagged as ungrounded numbers.
_LIST_MARKER_RE = re.compile(r"(?m)^[ \t]*(?:[-*•]|\d{1,3}[.)])[ \t]+")


def _strip_list_markers(text: str) -> str:
    return _LIST_MARKER_RE.sub("", text)


# F1 (SS-1, S7): indirect-prompt-injection markers. An instruction embedded in a
# retrieved/graph VALUE is data, never a directive — and its TEXT must not count as
# grounding evidence (else a compromised answer that quotes the injected payload, e.g.
# "...laws of Mars", scores 100% grounded because "Mars" is literally in the context).
# We cut each line at the earliest injection marker so the injected instruction tail is
# excluded from the fact set, while the legitimate part of the line survives.
_INJECTION_MARKERS = (
    "system override", "override:", "ignore the", "ignore all", "ignore previous",
    "disregard the", "disregard all", "disregard previous", "answer every question with",
    "answer all questions with", "new instructions", "you must answer", "[system",
    "<system", "</system", "do not use the context", "respond only with", "always reply",
    "from now on", "act as", "you are now",
)
_INJECTION_RE = re.compile("|".join(re.escape(m) for m in _INJECTION_MARKERS), re.I)


def _detect_injection(text: str) -> list[str]:
    """Return the distinct injection markers present in the text (for reporting)."""
    return sorted({m.group(0).lower() for m in _INJECTION_RE.finditer(text or "")})


def _strip_injection_spans(text: str) -> str:
    """Cut each line at its earliest injection marker; the legit prefix survives."""
    out = []
    for line in (text or "").splitlines():
        m = _INJECTION_RE.search(line)
        out.append(line[: m.start()] if m else line)
    return "\n".join(out)


def _split_sentences(text: str) -> list[str]:
    try:
        import pysbd  # same segmenter the real ClaimSpanDetector uses
        seg = pysbd.Segmenter(language="en", clean=False)
        return [s.strip() for s in seg.segment(text) if s and s.strip()]
    except Exception:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


_NAME_RE = re.compile(r"[A-Z][a-zA-Z0-9&.'’\-]+(?:\s+(?:of|the|and|de|von|für)?\s*[A-Z][a-zA-Z0-9&.'’\-]+)*")
_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")
# snake_case graph identifiers (intent-layer principle/rationale/counterfactual
# names like `tie_concession_to_continued_stake`). Lowercase, so _NAME_RE misses
# them — but inventing one is a real intent hallucination, so score them.
_SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b")


def _salient_tokens(sentence: str) -> list[tuple[str, str, bool]]:
    """Return [(kind, token, sentence_initial)] of name/number/acronym/id tokens worth
    grounding. ``sentence_initial`` is True only for a NAME match at the very start of the
    sentence — such a single Title-Case word may be a positional-capitalization artifact
    rather than an asserted entity (R1; resolved in score())."""
    out: list[tuple[str, str, bool]] = []
    for m in _NAME_RE.finditer(sentence):
        phrase = m.group(0).strip(" .,'’-")
        words = phrase.split()
        # R1-residual (session-4): strip leading determiner/stopwords from a multi-word
        # name ("The DAP" -> "DAP", "These Agreements" -> "Agreements") so a sentence-initial
        # stopword glued to a real acronym/name no longer makes the phrase look ungrounded.
        # The trailing acronym ("DAP") is still grounded via the acronym path.
        while len(words) > 1 and words[0].lower() in _CAP_STOP:
            words = words[1:]
        phrase = " ".join(words)
        if len(words) == 1:
            w = words[0]
            if w.lower() in _CAP_STOP or len(w) < 4 or w.isupper():
                continue
        out.append(("name", phrase, m.start() == 0))
    for m in _ACRONYM_RE.finditer(sentence):
        if m.group(0).lower() not in _CAP_STOP and len(m.group(0)) >= 2:
            out.append(("acronym", m.group(0), False))
    for m in _SNAKE_RE.finditer(sentence):
        out.append(("id", m.group(0), False))
    for m in _NUM_RE.finditer(sentence):
        tok = m.group(0)
        if re.search(r"\d", tok):
            out.append(("number", tok, False))
    # dedupe preserving order (keep the first occurrence's sentence-initial flag)
    seen = set()
    uniq = []
    for kind, tok, ini in out:
        k = (kind, tok.lower())
        if k not in seen:
            seen.add(k)
            uniq.append((kind, tok, ini))
    return uniq


def _token_grounded(kind: str, token: str, fact_norm: str) -> bool:
    """A token is grounded if it (or, for a multi-word name, each content word)
    appears in the normalized fact set."""
    tnorm = _normalize(token).strip()
    if not tnorm:
        return True
    if f" {tnorm} " in f" {fact_norm} " or tnorm in fact_norm:
        return True
    if kind == "name":
        words = [w for w in tnorm.split() if len(w) >= 4 and w not in _CAP_STOP]
        if words and all((f" {w} " in f" {fact_norm} " or w in fact_norm) for w in words):
            return True
    return False


# H-2 (acceptance swarm): Layer-3 epistemic check. Token grounding (Layer-1) and the
# graph edge check (Layer-2) are both BLIND to an inverted-polarity claim — stating a
# REJECTED alternative (Counterfactual, is_term=false) as if it were a real term. Both
# the rejected node and the term it was an alternative TO are grounded, so Layer-1
# passes (Agent-5 false-pass). This pure helper flags any sentence that asserts a
# rejected term WITHOUT a rejection marker. The caller supplies the rejected-term
# descriptors from the graph (keeps this module graph-free).
_REJECT_MARKERS = (
    "reject", "rejected", "alternative", "instead of", "rather than", "not chosen",
    "declined", "ruled out", "considered but", "would have", "was not adopted",
    "did not adopt", "counterfactual", "not selected", "passed over", "forwent",
    "chose not", "opted against", "turned down", "dismissed", "discarded",
)


def epistemic_violations(answer: str, rejected_terms: list[dict]) -> list[dict]:
    """rejected_terms: [{name, tokens:[...]}] — graph nodes with is_term=false /
    epistemic_status=rejected_alternative. Returns sentences that present a rejected
    term as real (mention it, no rejection marker). An empty list = epistemically clean."""
    out: list[dict] = []
    sentences = _split_sentences(_strip_list_markers(answer))
    for sent in sentences:
        norm = _normalize(sent)            # spaces between words, lowercased
        norm_sp = f" {norm} "
        for rt in rejected_terms:
            name = _normalize(rt.get("name") or "").strip()   # snake_case -> spaced words
            toks = [t.lower() for t in (rt.get("tokens") or []) if len(t) >= 4]
            name_in = bool(name) and f" {name} " in norm_sp     # exact id cited (whole phrase)
            present = [t for t in toks if f" {t} " in norm_sp]  # WHOLE-WORD only (no substring)
            # mentioned if the id is cited, or ≥2 distinctive content tokens co-occur
            mentioned = name_in or len(present) >= 2
            if not mentioned:
                continue
            # Fence check INDEPENDENT of the rejected term's own name: the cited id
            # (e.g. watson_pay_on_delivery_rejected) itself contains "rejected" — that
            # is NOT a fence. Strip the id, then look for a real rejection marker.
            fence_text = norm.replace(name, " ") if name else norm
            if any(mk in fence_text for mk in _REJECT_MARKERS):
                continue
            out.append({"sentence": sent, "rejected_term": rt.get("name"),
                        "matched_on": "name" if name_in else "tokens"})
            break
    return out


def score(answer: str, context: str, query: str, expect: list[str] | None = None,
          expect_abstain: bool = False, anti_expect: list[str] | None = None) -> dict:
    # F1: split the fact set into CONTEXT vs QUERY and strip injected-instruction spans
    # from the context. A positive (non-abstain) assertion must be grounded in the
    # CONTEXT; a token grounded ONLY via the query echoes the question as if it were a
    # fact (S2 query-echo) and is NOT faithful. Abstention may freely repeat query terms.
    injection_markers = _detect_injection(context)
    context_norm = _normalize(_strip_injection_spans(context))
    query_norm = _normalize(query or "")
    sentences = _split_sentences(_strip_list_markers(answer))
    sent_reports = []
    total_tokens = grounded_tokens = 0
    faithful_sentences = 0
    positive_assertions = 0
    query_echo_all: list[str] = []

    for sent in sentences:
        low = sent.lower()
        is_abstain = any(cue in low for cue in _ABSTAIN_CUES)
        negated = _sentence_negated(low)
        toks = _salient_tokens(sent)
        ungrounded = []
        query_echo = []
        salient_kept = []
        for kind, tok, is_initial in toks:
            in_ctx = _token_grounded(kind, tok, context_norm)
            in_q = _token_grounded(kind, tok, query_norm)
            # R1 (session-4): positional-capitalization demotion. A single-word,
            # sentence-initial, non-CamelCase, digit-free Title-Case "name" whose
            # lowercased form is a word from the QUERY (and is absent from the context)
            # is the user's own word capitalized only by sentence position — not an
            # asserted entity. Drop it (neither grounded-count nor hallucination-flag).
            # A genuinely hallucinated sentence-initial proper noun ("Florida governs…")
            # is NOT in the query, so it is still flagged.
            if (kind == "name" and is_initial and " " not in tok
                    and not _has_caps_or_digit(tok) and in_q and not in_ctx):
                continue
            total_tokens += 1
            salient_kept.append(tok)
            ok = in_ctx or (in_q and is_abstain)
            if ok:
                grounded_tokens += 1
            else:
                ungrounded.append(f"{kind}:{tok}")
                if in_q and not in_ctx and not is_abstain:
                    query_echo.append(f"{kind}:{tok}")
        query_echo_all.extend(query_echo)
        if not is_abstain and salient_kept:
            positive_assertions += 1
        # A sentence is faithful if it abstains, OR every salient token is grounded.
        sent_faithful = is_abstain or not ungrounded
        if sent_faithful:
            faithful_sentences += 1
        sent_reports.append({
            "text": sent,
            "abstain": is_abstain,
            "negated": negated,
            "salient": salient_kept,
            "ungrounded": ungrounded,
            "query_echo": query_echo,
            "faithful": sent_faithful,
        })

    completeness = None
    missing_expected = []
    if expect:
        ans_norm = _normalize(answer)
        present = [e for e in expect if _normalize(e).strip() in ans_norm]
        missing_expected = [e for e in expect if e not in present]
        completeness = round(len(present) / len(expect), 3) if expect else None

    # --expect-abstain gate — the answer must make no positive answer. R2 (session-4):
    # credit abstention when there is NO positive answer to the question, even if the
    # answer names other context facts to explain *why* it is refusing. A "positive
    # answer" is a non-abstaining, non-negated sentence that carries a salient token; a
    # sentence that only names distractors inside a dismissive clause ("…but the context
    # does not state…") does NOT count. (Old rule credited abstention only at zero
    # positive assertions, penalizing the more useful "refuse + explain" answer.)
    has_abstain = any(s["abstain"] for s in sent_reports)
    positive_answer = any(
        (not s["abstain"]) and (not s["negated"]) and s["salient"] for s in sent_reports)
    abstains_overall = (not positive_answer) and (has_abstain or positive_assertions == 0)
    expect_abstain_pass = abstains_overall if expect_abstain else None

    # F2: --anti-expect — tokens that must NOT appear in the answer (e.g. competing
    # jurisdictions present in the context as distractors). Catches distractor CAPTURE,
    # which Layer-1 grounding is blind to (a captured distractor is "grounded"). R2:
    # negation-aware — a token only violates if it appears in a sentence that positively
    # asserts it (not negated, not abstaining); naming a distractor to dismiss it is fine.
    anti_expect_violations = []
    if anti_expect:
        for a in anti_expect:
            anorm = _normalize(a).strip()
            if not anorm:
                continue
            hits = [s for s in sent_reports if anorm in _normalize(s["text"])]
            if hits and any((not s["negated"]) and (not s["abstain"]) for s in hits):
                anti_expect_violations.append(a)

    n = len(sentences) or 1
    faithfulness = round(faithful_sentences / n, 3)

    # R5 (session-4): a single folded verdict over what the SCORER can see (L1 token
    # grounding + query-echo + anti-expect + expect-abstain). regen_decompress.py
    # recomputes this to also fold Layer-2 (edge REFUTED) and Layer-3 (epistemic). The
    # headline `faithfulness` field alone is NOT a safe verdict under noise — read this.
    verdict_reasons = []
    if faithfulness < 1.0:
        verdict_reasons.append("ungrounded tokens (L1)")
    if query_echo_all:
        verdict_reasons.append("query-echo")
    if anti_expect_violations:
        verdict_reasons.append("anti-expect captured")
    if expect_abstain and not abstains_overall:
        verdict_reasons.append("expected abstention but answer asserted")
    overall_verdict = ("unfaithful" if verdict_reasons
                       else "abstained" if abstains_overall else "faithful")

    return {
        "overall_verdict": overall_verdict,
        "verdict_reasons": verdict_reasons,
        "faithfulness": faithfulness,
        "faithful_sentences": faithful_sentences,
        "total_sentences": len(sentences),
        # NOTE: injection markers are scanned in the CONTEXT (a per-prompt hazard flag),
        # NOT in the answer — identically populated for a faithful and a compromised
        # answer. The per-answer discriminator is faithfulness / query_echo / anti-expect.
        "injection_markers_in_context": injection_markers,
        "injection_markers_detected": injection_markers,  # back-compat alias
        "query_echo_tokens": sorted(set(query_echo_all)),
        "abstains_overall": abstains_overall,
        "expect_abstain_pass": expect_abstain_pass,
        "anti_expect_violations": anti_expect_violations,
        "token_grounding": round(grounded_tokens / total_tokens, 3) if total_tokens else 1.0,
        "grounded_tokens": grounded_tokens,
        "total_salient_tokens": total_tokens,
        "hallucinated_tokens": sorted({u for s in sent_reports for u in s["ungrounded"]}),
        "completeness": completeness,
        "missing_expected": missing_expected,
        "sentences": sent_reports,
    }


def _print_report(rep: dict, verbose: bool) -> None:
    if rep.get("overall_verdict"):
        vflag = {"faithful": "✓", "abstained": "✓", "unfaithful": "✗"}.get(rep["overall_verdict"], "?")
        rs = f"  ({', '.join(rep['verdict_reasons'])})" if rep.get("verdict_reasons") else ""
        print(f"OVERALL VERDICT {vflag} {rep['overall_verdict'].upper()}{rs}")
    fa = rep["faithfulness"]
    flag = "✓" if fa == 1.0 else ("⚠" if fa >= 0.5 else "✗")
    print(f"FAITHFULNESS {flag} {fa:.0%}  "
          f"({rep['faithful_sentences']}/{rep['total_sentences']} sentences clean)")
    print(f"TOKEN GROUNDING  {rep['token_grounding']:.0%}  "
          f"({rep['grounded_tokens']}/{rep['total_salient_tokens']} salient name/number tokens resolve in context)")
    if rep["hallucinated_tokens"]:
        print(f"  ✗ UNGROUNDED (not in context — possible hallucination): {rep['hallucinated_tokens']}")
    else:
        print("  ✓ every name/number in the answer is present in the grounded context")
    if rep.get("query_echo_tokens"):
        print(f"  ⚠ QUERY-ECHO (asserted as fact but only present in the QUESTION, not the context): "
              f"{rep['query_echo_tokens']}")
    if rep.get("injection_markers_detected"):
        print(f"  ⚠ INJECTION MARKERS in context (treated as data; excluded from the fact set): "
              f"{rep['injection_markers_detected']}")
    if rep.get("expect_abstain_pass") is not None:
        eflag = "✓" if rep["expect_abstain_pass"] else "✗"
        verdict = "abstained (correct rejection)" if rep["expect_abstain_pass"] else \
            "made a positive assertion (FAILED — expected rejection)"
        print(f"EXPECT-ABSTAIN {eflag} {verdict}")
    if rep.get("anti_expect_violations"):
        print(f"  ✗ ANTI-EXPECT VIOLATION (distractor token captured into the answer): "
              f"{rep['anti_expect_violations']}")
    if rep["completeness"] is not None:
        cflag = "✓" if rep["completeness"] == 1.0 else "⚠"
        print(f"COMPLETENESS {cflag} {rep['completeness']:.0%}"
              + (f"   missing: {rep['missing_expected']}" if rep["missing_expected"] else ""))
        print("  (completeness is a RETRIEVAL property — a faithful answer can be incomplete "
              "when retrieval under-delivers)")
    if verbose:
        print("\n--- per-sentence ---")
        for s in rep["sentences"]:
            tag = "ABSTAIN" if s["abstain"] else ("OK" if s["faithful"] else "HALLUC")
            print(f"  [{tag:>7}] {s['text'][:90]}")
            if s["ungrounded"]:
                print(f"            ungrounded: {s['ungrounded']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answer-file", help="answer text file (default: stdin)")
    ap.add_argument("--context-file", help="context text file")
    ap.add_argument("--query", default="", help="the user query (its terms are fair game)")
    ap.add_argument("--compose-json", help="regen_compose --json output (provides context+query)")
    ap.add_argument("--expect", help="comma-separated tokens a COMPLETE answer should contain")
    ap.add_argument("--expect-abstain", action="store_true", dest="expect_abstain",
                    help="negative-rejection gate: PASS only if the answer refuses (no positive assertion)")
    ap.add_argument("--anti-expect", dest="anti_expect",
                    help="comma-separated tokens that must NOT appear (distractor-capture catch)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    answer = Path(args.answer_file).read_text() if args.answer_file else sys.stdin.read()
    context = ""
    query = args.query
    if args.compose_json:
        d = json.loads(Path(args.compose_json).read_text())
        context = d.get("context", "")
        query = query or d.get("query", "")
    elif args.context_file:
        context = Path(args.context_file).read_text()
    expect = [e.strip() for e in args.expect.split(",") if e.strip()] if args.expect else None
    anti_expect = [e.strip() for e in args.anti_expect.split(",") if e.strip()] if args.anti_expect else None

    rep = score(answer, context, query, expect, expect_abstain=args.expect_abstain,
                anti_expect=anti_expect)
    if args.json:
        rep_out = {k: v for k, v in rep.items() if k != "sentences"}
        print(json.dumps(rep_out, indent=2))
    else:
        _print_report(rep, args.verbose)


if __name__ == "__main__":
    main()
