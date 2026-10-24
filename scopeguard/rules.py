"""Deterministic layer. It runs BEFORE any LLM call and its verdicts are final:

0. retractions and hypotheticals are never requests (UNCLEAR, never billed);
1. numeric caps in the SOW ("up to 24 images", "one address");
2. work the client supplies themselves (using it is included);
3. explicit SOW exclusions, matched by phrase (not by a single shared word);
4. revision-type requests, counted against the SOW's revision allowance;
5. the page counter.

Rules are cautious: when in doubt the answer is UNCLEAR, never a bill.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from .models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR, Clause, Request, Sow
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


_CREATE = re.compile(r"\b(write|create|design|shoot|take|produce|generate|film|record|draw|compose|prepare|source|"
                     r"find|buy|license|make (?:us )?(?:an?|new))\b", re.I)


def match_exclusion(text: str, sow: Sow) -> Optional[Clause]:
    """The exclusion clause whose phrase the request contains, or None.

    Matching is per phrase of the clause ("Logo or brand design" -> "Logo", "brand design"):
    a short phrase must be fully present in the request, a long one needs two content words.
    It also must not be explained better by a deliverable clause (so 'install Google Analytics'
    is not blocked by 'advanced analytics dashboards are not included')."""
    toks = set(concepts(text))
    best, score = None, 0
    for c in sow.clauses:
        if c.kind != "exclusion":
            continue
        if any(phrase_covered(toks, p) for p in phrases(c.text)):
            s = overlap(text, c.text)
            if s > score:
                best, score = c, s
    if best is None:
        return None
    deliv = max((overlap(text, c.text) for c in sow.clauses
                 if c.kind == "deliverable" and not _CLIENT_DUTY.search(c.text)), default=0)
    # a creation verb ("shoot photos", "write the text") makes the exclusion win ties against a
    # deliverable that merely mentions the same material
    return best if (score > deliv or (score == deliv and _CREATE.search(text))) else None


# ---------------------------------------------------------------- client-supplied assets
_USE = re.compile(r"\b(use|using|place|put|insert|upload|swap in|add|include|show|display|embed|drop in|publish)\b", re.I)
_SUPPLIED_BY_ME = re.compile(
    r"\b(?:i|we)(?:'ve|'ll| have| will)?\s+(?:just\s+)?(?:sent|send|attached|attach|provided|provide|supplied|supply|"
    r"uploaded|upload|shared|share|gave|give|emailed|email)\b|\b(?:my|our) own\b", re.I)


def client_supplied_rule(text: str, sow: Sow) -> Optional[dict]:
    """The SOW says the client supplies X; a request to USE or PLACE X (that the client says they
    sent) is the freelancer's included job, never a billable extra."""
    if not sow.client_supplied or _CREATE.search(text) or not _USE.search(text) or not _SUPPLIED_BY_ME.search(text):
        return None
    hit = {t for t in set(concepts(text)) & set(sow.client_supplied) if t.startswith("c:")}
    if not hit:
        return None
    c = sow.by_id(sow.client_clause)
    return {"cls": IN_SCOPE, "clause": c, "rule": "client_supplied", "confidence": 0.85,
            "reason": f"The SOW says the client supplies this ({c.cite()}); using or placing it is included."}


# ---------------------------------------------------------------- numeric caps
_NUMREQ = re.compile(r"\b(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|fifty)"
                     r"\s+(?:(?:email|extra|additional|more|new)\s+)?(?P<unit>[A-Za-z]+)\b", re.I)


def cap_rule(text: str, sow: Sow, within_ok: bool) -> Optional[dict]:
    """Compare numbers in the request with numeric limits in the SOW (N photos, one address, ...).
    OUT only when the request exceeds the cap; IN when it stays within it (never for revisions)."""
    from .sow import canonical_unit
    if not sow.caps:
        return None
    seen_ok = None
    for m in _NUMREQ.finditer(text):
        tok = m.group("n").lower()
        n = int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok)
        unit = canonical_unit(m.group("unit"))
        if not n or not unit:
            continue
        for cap in sow.caps:
            if cap["unit"] != unit:
                continue
            c = sow.by_id(cap["clause"])
            if n > cap["n"]:
                return {"cls": OUT_OF_SCOPE, "clause": c, "rule": "numeric_cap", "confidence": 0.85,
                        "reason": f"The request asks for {n} {unit}(s); {c.cite()} caps this at {cap['n']}."}
            seen_ok = {"cls": IN_SCOPE, "clause": c, "rule": "numeric_cap_ok", "confidence": 0.75,
                       "reason": f"{n} {unit}(s) is within the cap of {cap['n']} in {c.cite()}."}
    return seen_ok if within_ok else None


# ---------------------------------------------------------------- page counter
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
    already mentions (a gallery, a contact form) is never counted as an extra page."""
    if not sow.page_count or not sow.page_names or not _PAGE_VERB.search(text) or _PAGE_DESTRUCTIVE.search(text):
        return None
    pc = sow.by_id(sow.page_clause) if sow.page_clause else None
    listed = set(sow.page_names)
    deliv_tokens = set()
    for c in sow.clauses:
        if c.kind == "deliverable":
            deliv_tokens |= set(concepts(c.text))
    new_name = None
    m = _PAGE_NEW.search(text)
    if m:
        new_name = m.group(0).lower()
    else:
        intro = _PAGE_INTRO.search(text)
        if intro and intro.group(1).lower() not in _PAGE_GENERIC and intro.group(1).lower() not in listed:
            if set(concepts(intro.group(1))) & deliv_tokens:
                return None            # the SOW already covers it: not a new page
            new_name = intro.group(1).lower()
    if new_name:
        seen = state.setdefault("new_pages", [])
        slots = sow.page_count - len(sow.page_names)
        if new_name not in seen:
            seen.append(new_name)
        if seen.index(new_name) + 1 > slots:
            return {"cls": OUT_OF_SCOPE, "clause": pc, "rule": "page_count", "confidence": 0.85,
                    "reason": f"The SOW lists {sow.page_count} pages ({', '.join(sow.page_names)}); "
                              f"a further page ('{new_name}') goes past that count."}
        return {"cls": IN_SCOPE, "clause": pc, "rule": "page_listed", "confidence": 0.8,
                "reason": f"Within the {sow.page_count} pages in the SOW (page {len(sow.page_names) + seen.index(new_name) + 1})."}
    for mm in _PAGE_MENTION.finditer(text):
        name = mm.group(1).lower()
        if name in listed:
            return {"cls": IN_SCOPE, "clause": pc, "rule": "page_listed", "confidence": 0.85,
                    "reason": f"The {name.title()} page is one of the {sow.page_count} pages the SOW lists."}
    return None


def precheck(req: Request, sow: Sow, state: Optional[dict] = None) -> Optional[dict]:
    """Final verdict from rules alone, or None when the LLM should decide.

    Returns {cls, clause, rule, confidence, reason}. Doubt is UNCLEAR, never a bill."""
    state = {} if state is None else state
    text = req.text()
    if req.act != "request":
        why = ("retracted by the client" if req.act == "retracted"
               else "hypothetical, a price question or curiosity")
        return {"cls": UNCLEAR, "clause": None, "rule": "not_a_request", "confidence": 0.9,
                "reason": f"Not a request: {why}. Never billed."}
    over = cap_rule(text, sow, within_ok=False)
    if over is not None:
        return over
    if not req.is_revision:
        cs = client_supplied_rule(text, sow)
        if cs is not None:
            return cs
        ex = match_exclusion(text, sow)
        if ex is not None:
            return {"cls": OUT_OF_SCOPE, "clause": ex, "rule": "explicit_exclusion", "confidence": 0.9,
                    "reason": f"The SOW explicitly excludes this ({ex.cite()})."}
        ok = cap_rule(text, sow, within_ok=True)
        if ok is not None:
            return ok
        return page_rule(text, sow, state)
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
