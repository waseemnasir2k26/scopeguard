"""Deterministic layer. It runs BEFORE any LLM call and its verdicts are final:

1. explicit SOW exclusions, matched by phrase (not by a single shared word);
2. revision-type requests, counted against the SOW's revision allowance.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from .models import IN_SCOPE, OUT_OF_SCOPE, Clause, Request, Sow
from .textutil import concepts, overlap, phrase_covered, phrases

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
    return best if score > deliv else None


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
    """Page counter. A request that names a page the SOW lists is IN-SCOPE; a request for a page
    the SOW does not list is OUT-OF-SCOPE once it pushes past the SOW's page count."""
    if not sow.page_count or not sow.page_names or not _PAGE_VERB.search(text) or _PAGE_DESTRUCTIVE.search(text):
        return None
    pc = sow.by_id(sow.page_clause) if sow.page_clause else None
    listed = set(sow.page_names)
    new_name = None
    m = _PAGE_NEW.search(text)
    if m:
        new_name = m.group(0).lower()
    else:
        intro = _PAGE_INTRO.search(text)
        if intro and intro.group(1).lower() not in _PAGE_GENERIC and intro.group(1).lower() not in listed:
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

    Returns {cls, clause, rule, confidence, reason}."""
    state = {} if state is None else state
    if not req.is_revision:
        ex = match_exclusion(req.text(), sow)
        if ex is not None:
            return {"cls": OUT_OF_SCOPE, "clause": ex, "rule": "explicit_exclusion", "confidence": 0.9,
                    "reason": f"The SOW explicitly excludes this ({ex.cite()})."}
        return page_rule(req.text(), sow, state)
    # revision-type request: governed by the revision allowance, never by an exclusion keyword
    if not sow.revision_limit:
        return None
    rc = sow.by_id(sow.revision_clause) if sow.revision_clause else None
    n = req.revision_round or 1
    if n <= sow.revision_limit:
        return {"cls": IN_SCOPE, "clause": rc, "rule": "revision_allowance", "confidence": 0.9,
                "reason": f"A revision of existing work: round {n} of the {sow.revision_limit} included."}
    return {"cls": OUT_OF_SCOPE, "clause": rc, "rule": "revision_limit", "confidence": 0.9,
            "reason": (f"Revision round {n}; the SOW includes {sow.revision_limit}. Rounds are counted "
                       f"heuristically (messages less than {ROUND_GAP_MINUTES} minutes apart are one round).")}
