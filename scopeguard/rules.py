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


def precheck(req: Request, sow: Sow) -> Optional[dict]:
    """Final verdict from rules alone, or None when the LLM should decide.

    Returns {cls, clause, rule, confidence, reason}."""
    if not req.is_revision:
        ex = match_exclusion(req.text(), sow)
        if ex is not None:
            return {"cls": OUT_OF_SCOPE, "clause": ex, "rule": "explicit_exclusion", "confidence": 0.9,
                    "reason": f"The SOW explicitly excludes this ({ex.cite()})."}
        return None
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
