"""Deterministic rule layer. Runs after the LLM and wins over it for two cases:
explicit SOW exclusions and revision-count limits."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from .models import OUT_OF_SCOPE, Finding, Request, Sow
from .textutil import overlap

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


def match_exclusion(quote: str, sow: Sow):
    """Best explicit-exclusion clause for a request, or None. Fires only when the exclusion
    overlaps strictly more than any deliverable clause (so 'add analytics' is not blocked by an
    exclusion about advanced analytics when a deliverable clearly covers it)."""
    best, score = None, 0
    for c in sow.clauses:
        if c.kind == "exclusion":
            s = overlap(quote, c.text)
            if s > score:
                best, score = c, s
    if best is None or score < 2:
        return None
    deliv = max((overlap(quote, c.text) for c in sow.clauses
                   if c.kind == "deliverable" and not _CLIENT_DUTY.search(c.text)), default=0)
    return best if score > deliv else None


def apply_rules(f: Finding, sow: Sow) -> Finding:
    """Mutates and returns the finding."""
    # Revisions of existing work ("make the logo bigger") are governed by the revision limit,
    # not by exclusions such as "logo design is not included".
    ex = None if f.request.is_revision else match_exclusion(f.request.quote, sow)
    if ex is not None:
        if f.cls != OUT_OF_SCOPE or f.clause_id != ex.id:
            f.notes.append(f"Rule layer overrode the model ({f.llm_class or f.cls} -> {OUT_OF_SCOPE}).")
        f.cls, f.clause_id, f.clause_text, f.clause_ref = OUT_OF_SCOPE, ex.id, ex.text, ex.ref
        f.rule = "explicit_exclusion"
        f.confidence = max(f.confidence, 0.9)
        f.reason = f"The SOW explicitly excludes this ({ex.cite()}). " + (f.reason if f.llm_class == OUT_OF_SCOPE else "")
        return f
    if sow.revision_limit and f.request.is_revision and f.request.revision_round > sow.revision_limit:
        rc = sow.by_id(sow.revision_clause) if sow.revision_clause else None
        if f.cls != OUT_OF_SCOPE:
            f.notes.append(f"Rule layer overrode the model ({f.llm_class or f.cls} -> {OUT_OF_SCOPE}).")
        f.cls = OUT_OF_SCOPE
        if rc:
            f.clause_id, f.clause_text, f.clause_ref = rc.id, rc.text, rc.ref
        f.rule = "revision_limit"
        f.confidence = max(f.confidence, 0.9)
        f.reason = (f"Revision round {f.request.revision_round} of this project; the SOW includes "
                    f"{sow.revision_limit}. Rounds are counted heuristically (messages less than "
                    f"{ROUND_GAP_MINUTES} minutes apart are one round).")
    return f
