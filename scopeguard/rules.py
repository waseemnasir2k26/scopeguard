"""Deterministic layer. It runs BEFORE any LLM call and its verdicts are final.

In order:
 0. withdrawn items, and retractions / hedges / jokes / hypotheticals: never requests;
 1. numeric caps in the SOW, compared with the most relevant cap only;
 2. work the client supplies or authored themselves (using it is included);
 3. explicit exclusions by phrase, with the SOW's own carve-outs ("except ..., which is included");
 4. revision requests against the revision allowance;
 5. the page counter; 6. a plain match with a listed deliverable.

Rules are cautious: when in doubt the answer is UNCLEAR, never a bill.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from .acts import ACT_WHY
from .models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR, Clause, Request, Sow
from .quantity import Quantity, is_supplied_total, parse_quantities
from .textutil import NUMBER_WORDS, concepts, overlap, phrase_covered, phrases

ROUND_GAP_MINUTES = 30
_CLIENT_DUTY = re.compile(r"\b(client|customer)\b.{0,40}\b(suppl|provid|send|deliver)", re.I)


def assign_revision_rounds(requests: Iterable[Request], gap_minutes: int = ROUND_GAP_MINUTES) -> int:
    """Group revision requests into rounds: same message, or messages less than gap_minutes apart.
    Returns the number of rounds."""
    rounds = 0
    last_msg = None
    last_ts = None
    for r in requests:
        if not r.is_revision:
            continue
        new = True
        if last_msg is not None:
            if r.message_id == last_msg:
                new = False
            elif r.ts and last_ts:
                try:
                    gap = (datetime.fromisoformat(r.ts) - datetime.fromisoformat(last_ts)).total_seconds() / 60
                    new = gap > gap_minutes
                except ValueError:
                    new = True
        if new:
            rounds += 1
        r.revision_round = rounds
        last_msg, last_ts = r.message_id, r.ts or last_ts
    return rounds


def rounds_log(requests: Iterable[Request]) -> list[str]:
    """Human-readable list of the revision rounds that were counted, with dates."""
    by_round: dict[int, list[Request]] = {}
    for r in requests:
        if r.is_revision and r.revision_round:
            by_round.setdefault(r.revision_round, []).append(r)
    out = []
    for n in sorted(by_round):
        rs = by_round[n]
        date = next((r.ts[:10] for r in rs if r.ts), "date unknown")
        out.append(f"Round {n} ({date}): {len(rs)} change request(s), first: \"{rs[0].quote[:70]}\"")
    return out


# ---------------------------------------------------------------- creation verbs / client-authored assets
_CREATE = re.compile(r"\b(write|create|design|shoot|take|produce|generate|film|record|draw|compose|prepare|source|"
                     r"find|buy|license|make (?:us )?(?:an?|new))\b", re.I)
_FIRST_PERSON_ACT = re.compile(r"\b(?:i|we)(?:'ve|'ll| have| had| will)?\s+(?:just\s+|already\s+)?\w+\b", re.I)
_USE = re.compile(r"\b(use|using|place|put|insert|upload|swap in|add|include|show|display|embed|drop|publish)\b", re.I)
_SUPPLIED_BY_ME = re.compile(
    r"\b(?:i|we)(?:'ve|'ll| have| had| will)?\s+(?:just\s+|already\s+)?(?:sent|send|attached|attach|provided|provide|"
    r"supplied|supply|uploaded|upload|shared|share|gave|give|emailed|email|designed|made|created|written|wrote|"
    r"drawn|taken|took|shot|produced|prepared|got|have)\b|\b(?:my|our) own\b", re.I)


def match_exclusion(text: str, sow: Sow) -> Optional[Clause]:
    """The exclusion clause whose phrase the request contains, or None (carve-outs do not count)."""
    kind, clause = exclusion_verdict(text, sow)
    return clause if kind == "exclude" else None


def exclusion_verdict(text: str, sow: Sow):
    """('exclude', clause) | ('carve', clause) | (None, None).

    Matching is per phrase of the clause ("Logo or brand design" -> "Logo", "brand design"): a short
    phrase must be fully present in the request, a long one needs two content words. A deliverable
    that explains the request better wins (so 'install Google Analytics' is not blocked by 'advanced
    analytics dashboards are not included'); a creation verb breaks a tie for the exclusion. A request
    that matches the SOW's own exception ("a blog (except one launch post, which is included)") is carved out."""
    toks = set(concepts(text))
    best, score, carve = None, 0, None
    for c in sow.clauses:
        if c.kind != "exclusion":
            continue
        if not any(phrase_covered(toks, p) for p in phrases(c.core_text())):
            continue
        if c.exceptions and any(phrase_covered(toks, e) for e in c.exceptions):
            carve = carve or c
            continue
        s = overlap(text, c.core_text())
        if s > score:
            best, score = c, s
    if best is None:
        return ("carve", carve) if carve else (None, None)
    deliv = max((overlap(text, c.text) for c in sow.clauses
                 if c.kind == "deliverable" and not _CLIENT_DUTY.search(c.text)), default=0)
    if score > deliv or (score == deliv and _CREATE.search(text)):
        return "exclude", best
    return None, None


def client_supplied_rule(text: str, sow: Sow) -> Optional[dict]:
    """The SOW says the client supplies X; a request to USE or PLACE X that the client sent or made
    themselves ("the logo I designed") is the freelancer's included job, never a billable extra."""
    if not sow.client_supplied or not _USE.search(text) or not _SUPPLIED_BY_ME.search(text):
        return None
    if _CREATE.search(_FIRST_PERSON_ACT.sub(" ", text)):
        return None                       # still asks the freelancer to create something
    hit = {t for t in set(concepts(text)) & set(sow.client_supplied) if t.startswith("c:")}
    if not hit:
        return None
    c = sow.by_id(sow.client_clause)
    return {"cls": IN_SCOPE, "clause": c, "rule": "client_supplied", "confidence": 0.85,
            "reason": f"The SOW says the client supplies this ({c.cite()}); using or placing it is included."}


# ---------------------------------------------------------------- numeric caps
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|https?://\S+")
_UNIT_KEYS = {"image": {"c:image"}, "email": {"email", "address", "recipient"}, "form": {"c:form", "form"},
              "language": {"c:translation", "language"}, "post": {"post"}, "video": {"c:production", "video"}}


def _ctx(text: str, clause_text: str, unit: str) -> int:
    """How many context words (the gallery, the contact form...) the request shares with a cap's clause,
    not counting the unit itself."""
    drop = _UNIT_KEYS.get(unit, {unit})
    return len((set(concepts(text)) - drop) & (set(concepts(clause_text)) - drop))


def cap_rule(text: str, sow: Sow, within_ok: bool) -> Optional[dict]:
    """Compare numbers in the request with the SOW's numeric limits (N images, one address ...).

    Only the most RELEVANT cap of the same unit counts (the one whose clause shares context with the
    request); numbers the client merely HAS or SENT ("I sent 40", "best 20 of 40") are not requests;
    "per row" densities and "minimum N" floors are not caps. OUT only when the request exceeds the cap."""
    from .sow import canonical_unit
    if not sow.caps:
        return None
    qs: list[Quantity] = [q for q in parse_quantities(text, canonical_unit)
                          if not q.per and not is_supplied_total(q.prev)]
    emails = {e.lower() for e in _EMAIL.findall(text)}
    if len(emails) >= 2:
        qs.append(Quantity(n=len(emails), unit_word="addresses"))
    seen_ok = None
    for q in qs:
        unit = canonical_unit(q.unit_word)
        relevant = [(c, _ctx(text, sow.by_id(c["clause"]).text, unit)) for c in sow.caps if c["unit"] == unit]
        relevant = [(c, k) for c, k in relevant if k >= 1]
        if not relevant:
            continue
        cap = max(relevant, key=lambda ck: ck[1])[0]
        c = sow.by_id(cap["clause"])
        if q.more and not q.vague:
            return {"cls": UNCLEAR, "clause": c, "rule": "incremental_quantity", "confidence": 0.8,
                    "reason": f"Asks for {q.n} MORE {unit}(s); the total depends on how many there already are "
                              f"({c.cite()} caps this at {cap['n']}). Ask the client."}
        if q.vague or q.n is None:
            return {"cls": UNCLEAR, "clause": c, "rule": "vague_quantity", "confidence": 0.8,
                    "reason": f"'A couple'/'a few' {unit}s is not a number; {c.cite()} caps this at {cap['n']}. Ask how many."}
        if q.n > cap["n"]:
            return {"cls": OUT_OF_SCOPE, "clause": c, "rule": "numeric_cap", "confidence": 0.85,
                    "reason": f"The request asks for {q.n} {unit}(s); {c.cite()} caps this at {cap['n']}."}
        seen_ok = {"cls": IN_SCOPE, "clause": c, "rule": "numeric_cap_ok", "confidence": 0.75,
                   "reason": f"{q.n} {unit}(s) is within the cap of {cap['n']} in {c.cite()}."}
    return seen_ok if within_ok else None


# ---------------------------------------------------------------- page counter
_ORDINALS = {"sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "6th": 6, "7th": 7, "8th": 8,
             "9th": 9, "10th": 10}
_PAGE_NEW = re.compile(
    r"\b(?:another|an?\s+(?:extra|additional|separate)|(?:sixth|seventh|eighth|ninth|tenth|6th|7th|8th|9th|10th)|"
    r"\d+\s+(?:more|extra|new|additional))\s+(?:\w+\s+)?pages?\b", re.I)
_PAGE_INTRO = re.compile(r"\b(?:an?|new|another|extra|additional)\s+(?:new\s+)?([A-Za-z][\w&-]*)\s+page\b", re.I)
_PAGE_MENTION = re.compile(r"\b([A-Za-z][\w&-]*)\s+(?:page|section)\b", re.I)
_PAGE_VERB = re.compile(r"\b(add|create|build|need|want|include|have|make|set ?up|put|insert)\b", re.I)
_PAGE_DESTRUCTIVE = re.compile(r"\b(remove|delete|drop|redesign|rebuild|replace|merge|split)\b", re.I)
_PAGE_GENERIC = {"the", "a", "an", "our", "this", "that", "each", "every", "whole", "new", "extra", "first", "last",
                 "same", "single", "landing", "main", "other", "separate", "additional", "another", "web", "one"}


def page_rule(text: str, sow: Sow, state: dict) -> Optional[dict]:
    """Page counter. A request that names a page the SOW lists is IN-SCOPE; a request for a genuinely
    new page is OUT-OF-SCOPE once it pushes past the SOW's page count. Anything a deliverable
    already mentions (a gallery, a contact form) is never counted as an extra page. When the SOW gives
    only a count (no page names) just an ordinal beyond it ("a sixth page") or an explicit "extra page" counts."""
    if not sow.page_count or not _PAGE_VERB.search(text) or _PAGE_DESTRUCTIVE.search(text):
        return None
    pc = sow.by_id(sow.page_clause) if sow.page_clause else None
    names = ", ".join(sow.page_names) if sow.page_names else "names not given"
    listed = set(sow.page_names)
    deliv_tokens = set()
    for c in sow.clauses:
        if c.kind == "deliverable":
            deliv_tokens |= set(concepts(c.text))
    new_name = None
    m = _PAGE_NEW.search(text)
    if m:
        new_name = m.group(0).lower()
        word = new_name.split()[0]
        if word in _ORDINALS and _ORDINALS[word] <= sow.page_count:
            return None
    elif sow.page_names:
        intro = _PAGE_INTRO.search(text)
        if intro and intro.group(1).lower() not in _PAGE_GENERIC and intro.group(1).lower() not in listed:
            if set(concepts(intro.group(1))) & deliv_tokens:
                return None            # the SOW already covers it: not a new page
            new_name = intro.group(1).lower()
    if new_name:
        seen = state.setdefault("new_pages", [])
        slots = max(0, sow.page_count - len(sow.page_names)) if sow.page_names else 0
        if new_name not in seen:
            seen.append(new_name)
        if seen.index(new_name) + 1 > slots:
            return {"cls": OUT_OF_SCOPE, "clause": pc, "rule": "page_count", "confidence": 0.85,
                    "reason": f"The SOW lists {sow.page_count} pages ({names}); "
                              f"a further page ('{new_name}') goes past that count."}
        return {"cls": IN_SCOPE, "clause": pc, "rule": "page_listed", "confidence": 0.8,
                "reason": f"Within the {sow.page_count} pages in the SOW (page {len(sow.page_names) + seen.index(new_name) + 1})."}
    for mm in _PAGE_MENTION.finditer(text):
        name = mm.group(1).lower()
        if name in listed:
            return {"cls": IN_SCOPE, "clause": pc, "rule": "page_listed", "confidence": 0.85,
                    "reason": f"The {name.title()} page is one of the {sow.page_count} pages the SOW lists."}
    return None


def deliverable_rule(text: str, sow: Sow) -> Optional[dict]:
    """A plain match with one phrase of a listed deliverable ("page titles", "social links in the footer").
    IN-SCOPE never costs the client money, so this runs last and only on whole phrases."""
    toks = set(concepts(text))
    page_tokens = set()
    for n in sow.page_names:
        page_tokens |= set(concepts(n))
    best, best_n = None, 0
    for c in sow.clauses:
        if c.kind != "deliverable" or _CLIENT_DUTY.search(c.text):
            continue
        for p in phrases(c.text):
            ptoks = set(concepts(p))
            if not ptoks or ptoks <= page_tokens:
                continue
            if phrase_covered(toks, p) and (len(ptoks) >= 2 or any(t.startswith("c:") for t in ptoks)):
                if len(ptoks) > best_n:
                    best, best_n = c, len(ptoks)
    if best is None:
        return None
    return {"cls": IN_SCOPE, "clause": best, "rule": "deliverable_match", "confidence": 0.7,
            "reason": f"The request matches a listed deliverable ({best.cite()})."}


def precheck(req: Request, sow: Sow, state: Optional[dict] = None) -> Optional[dict]:
    """Final verdict from rules alone, or None when the LLM should decide.

    Returns {cls, clause, rule, confidence, reason}. Doubt is UNCLEAR, never a bill."""
    state = {} if state is None else state
    text = req.text()
    if req.withdrawn_by:
        return {"cls": UNCLEAR, "clause": None, "rule": "withdrawn", "confidence": 0.9,
                "reason": f"Withdrawn by the client later: \"{req.withdrawn_by[:100]}\". Never billed."}
    if req.hedged:
        return {"cls": UNCLEAR, "clause": None, "rule": "conditional", "confidence": 0.85,
                "reason": f"Hedged by the client (\"{req.hedged[:80]}\"). Needs your call; never billed."}
    if req.conflict:
        return {"cls": UNCLEAR, "clause": None, "rule": "conflicting", "confidence": 0.9,
                "reason": f"Conflicting statements in one message (\"{req.conflict[:100]}\"). Ask the client what they want."}
    if req.act != "request":
        return {"cls": UNCLEAR, "clause": None, "rule": "not_a_request", "confidence": 0.9,
                "reason": f"Not a request: {ACT_WHY.get(req.act, req.act)}. Needs your call; never billed."}
    over = cap_rule(text, sow, within_ok=False)
    if over is not None:
        return over
    text = _ADDRESS.sub(" ", text)        # "sales@host" must not trigger the shop exclusion
    if not req.is_revision:
        cs = client_supplied_rule(text, sow)
        if cs is not None:
            return cs
        kind, ex = exclusion_verdict(text, sow)
        if kind == "carve":
            if ex.exception_included:
                return {"cls": IN_SCOPE, "clause": ex, "rule": "sow_exception", "confidence": 0.85,
                        "reason": f"The SOW carves this out of an exclusion and says it is included ({ex.cite()})."}
            return {"cls": UNCLEAR, "clause": ex, "rule": "sow_exception", "confidence": 0.7,
                    "reason": f"{ex.cite()} makes an exception to this exclusion; read the clause."}
        if kind == "exclude":
            return {"cls": OUT_OF_SCOPE, "clause": ex, "rule": "explicit_exclusion", "confidence": 0.9,
                    "reason": f"The SOW explicitly excludes this ({ex.cite()})."}
        ok = cap_rule(text, sow, within_ok=True)
        if ok is not None:
            return ok
        pg = page_rule(text, sow, state)
        if pg is not None:
            return pg
        return deliverable_rule(text, sow)
    # revision-type request: governed by the revision allowance, never by an exclusion keyword
    if not sow.revision_limit:
        return None
    rc = sow.by_id(sow.revision_clause) if sow.revision_clause else None
    n = req.revision_round or 1
    if n <= sow.revision_limit:
        return {"cls": IN_SCOPE, "clause": rc, "rule": "revision_allowance", "confidence": 0.9,
                "reason": f"A revision of existing work: round {n} of the {sow.revision_limit} included."}
    return {"cls": OUT_OF_SCOPE, "clause": rc, "rule": "revision_limit", "confidence": 0.9,
            "reason": (f"By my count this is revision round {n}; the SOW includes {sow.revision_limit}. Rounds are "
                       f"counted heuristically (messages less than {ROUND_GAP_MINUTES} minutes apart are one round).")}
