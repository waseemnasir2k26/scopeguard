"""Repeat check: a reworded repeat of an item you already BILLED or DISMISSED must not come back as a fresh
possible extra. "Could you add a Pricing page for the studio on the site?" is the same ask as "Please add a
Pricing page for the studio to the site." billed under another id: it goes to "Needs your call" instead.

Match rule (applied to each quote of the new item against each ledger entry's quote):
  * content-word Jaccard >= 0.5 (stemmed, stopwords removed), or
  * the same cited clause AND at least 2 shared content words."""
from __future__ import annotations

import re

_STOP = set("""
a an the and or but if so of to in on at by for with from as is are was were be been being am do does did done
can could would should will shall may might must you your yours we our ours us i me my mine it its this that these
those there here they them their he she his her not no yes ok okay please pls plz thanks thank hi hello hey
also just really very much some any one up down out over into about what when where which who how why get got
let lets make want wants need needs like sure maybe think hmm well actually then than too still again all each
both few other such only own same have has had having provide provided include included including
# generic request and deliverable words: on almost every SOW line, so they never make two asks the same
add adds added adding change make new page pages site sites website websites section
""".split())
_WORD = re.compile(r"[a-z0-9]+")
JACCARD_MIN = 0.5
SHARED_MIN = 2


def _stem(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ing") and len(w) - 3 >= 3:
        return w[:-3]
    if w.endswith("ed") and len(w) - 2 >= 3:
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def content_words(text: str) -> frozenset:
    out = set()
    for raw in _WORD.findall((text or "").lower()):
        if raw in _STOP or raw.isdigit():
            continue
        w = _stem(raw)
        if len(w) >= 3 and w not in _STOP:
            out.add(w)
    return frozenset(out)


def is_repeat(quote: str, clause: str, old_quote: str, old_clause: str) -> bool:
    a, b = content_words(quote), content_words(old_quote)
    if not a or not b:
        return False
    shared = len(a & b)
    if shared / len(a | b) >= JACCARD_MIN:
        return True
    return bool(clause) and clause == old_clause and shared >= SHARED_MIN


def find_repeat(quotes: list, clause: str, ledger: dict):
    """The first ledger entry (billed or dismissed) that the new item repeats, as (fingerprint, entry), or None.
    Each quote of the item is compared, so a consolidated item cannot hide a repeated part."""
    for fp, entry in ledger.items():
        old = entry.get("quote") or ""
        if not old or not entry.get("decision") in ("billed", "dismissed"):
            continue
        if any(is_repeat(q, clause, old, entry.get("clause") or "") for q in quotes):
            return fp, entry
    return None
