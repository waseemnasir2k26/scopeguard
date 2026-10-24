"""Find quantities like "thirty photos", "2 dozen images", "one address" or "a couple of pages" in text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SMALL = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_VAGUE = {"couple", "few", "several", "handful", "bunch", "number"}
_MODIFIERS = {"extra", "more", "additional", "new", "client-supplied", "email", "further", "other", "separate",
              "different", "full", "high-resolution", "good", "nice", "great", "best", "supplied", "client", "of"}
_STOP_BEFORE_UNIT = {"and", "or", "but", "to", "for", "from", "in", "on", "with", "per", "at", "by", "than"}
_DENSITY = {"per", "each", "every", "a", "an"}
# words that mark a number as something the client HAS or SENT (a supplied total), not what they ask for
SUPPLIED_MARKERS = {"of", "from", "sent", "provided", "uploaded", "attached", "gave", "own", "out", "supplied"}
_WEAK_MARKERS = {"have", "had", "has", "got", "send", "sending", "upload", "share", "shared", "give"}


def is_supplied_total(prev: list) -> bool:
    """True when the number is what the client already HAS or SENT ("I sent 40", "I have 100", "best 20 of 40"),
    not what they ask for. "the gallery have 30 images" and "form send enquiries to two addresses" are requests,
    so have/send need a first-person subject."""
    if any(w in SUPPLIED_MARKERS for w in prev):
        return True
    return any(w in _WEAK_MARKERS for w in prev) and any(w in ("i", "we", "ve", "ll", "they") for w in prev)


_QTY_PREV = {"best", "top", "first", "only", "just", "about", "around", "roughly", "show", "display", "use",
             "want", "need", "total", "include", "fit", "max", "most", "pick", "choose", "select", "keep", "with"}
_TOKEN = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*|\d[\d,]*(?:\.\d+)?")


@dataclass
class Quantity:
    n: int | None            # None for vague quantities ("a couple")
    unit_word: str
    vague: bool = False
    prev: list = field(default_factory=list)   # the three words before the number
    per: bool = False        # "24 photos per page": a density, not a total
    more: bool = False       # "2 dozen MORE photos": an increment, so the total is unknown


def _num_word(tok: str) -> int | None:
    if tok in _SMALL:
        return _SMALL[tok]
    if tok in _TENS:
        return _TENS[tok]
    if "-" in tok:
        a, _, b = tok.partition("-")
        if a in _TENS and b in _SMALL and 0 < _SMALL[b] < 10:
            return _TENS[a] + _SMALL[b]
    return None


def _parse_number(words: list[str], i: int):
    """Return (value, tokens_consumed) for a number starting at words[i], or None. Handles digits,
    spelled numbers up to 999,999 ("twenty-five", "one hundred and ten"), "a dozen", "two dozen"."""
    w = words[i]
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", w):
        try:
            val = float(w.replace(",", ""))
        except ValueError:
            return None
        used = 1
        if i + 1 < len(words) and words[i + 1] in ("dozen", "hundred", "thousand"):
            val *= {"dozen": 12, "hundred": 100, "thousand": 1000}[words[i + 1]]
            used = 2
        return int(val), used
    if w in ("a", "an") and i + 1 < len(words) and words[i + 1] in ("dozen", "hundred", "thousand"):
        return {"dozen": 12, "hundred": 100, "thousand": 1000}[words[i + 1]], 2
    total, current, used, seen = 0, 0, 0, False
    j = i
    while j < len(words):
        t = words[j]
        v = _num_word(t)
        if v is not None:
            if seen and current and v < 100 and (current % 100) != 0 and t not in _TENS and v >= 10:
                break
            current += v
            seen = True
        elif t == "hundred" and seen:
            current = max(current, 1) * 100
        elif t == "thousand" and seen:
            total += max(current, 1) * 1000
            current = 0
        elif t == "dozen" and seen:
            current *= 12
        elif t == "and" and seen and j + 1 < len(words) and _num_word(words[j + 1]) is not None and current >= 100:
            pass
        else:
            break
        j += 1
        used = j - i
    if not seen:
        return None
    return total + current, used


def parse_quantities(text: str, unit_of) -> list[Quantity]:
    """unit_of(word) -> canonical unit or None. Returns the quantities attached to a known unit."""
    toks = [m.group(0).lower() for m in _TOKEN.finditer(text or "")]
    out: list[Quantity] = []
    loose: list = []
    i = 0
    while i < len(toks):
        prev = toks[max(0, i - 3):i]
        vague = False
        n = None
        used = 0
        if toks[i] in _VAGUE and (i == 0 or toks[i - 1] in ("a", "the", "of")):
            vague, used = True, 1
        else:
            got = _parse_number(toks, i)
            if got:
                n, used = got
        if used:
            j = i + used
            unit = None
            skipped = 0
            more = any(w in ("another", "additional", "extra", "further") for w in prev[-1:])
            while j < len(toks) and skipped < 4:
                t = toks[j]
                if unit_of(t):
                    unit = t
                    break
                if t in _STOP_BEFORE_UNIT:
                    break
                if t in ("more", "extra", "additional", "further"):
                    more = True
                if t in _MODIFIERS or skipped < 2:
                    j += 1
                    skipped += 1
                    continue
                break
            if unit:
                nxt = toks[j + 1] if j + 1 < len(toks) else ""
                out.append(Quantity(n=n, unit_word=unit, vague=vague, prev=prev, per=nxt in _DENSITY, more=more))
                i = j + 1
                continue
            if n is not None and any(t in _QTY_PREV for t in prev):
                loose.append((n, prev))
            i += used
            continue
        i += 1
    # "use the best 20 of my 40 photos": a bare number takes the sentence's only unit, when a
    # quantity word ("best", "only", "show") precedes it
    units = {unit_of(t) for t in toks if unit_of(t)}
    if len(units) == 1 and loose:
        word = next(t for t in toks if unit_of(t))
        for n, prev in loose:
            out.append(Quantity(n=n, unit_word=word, prev=prev))
    return out
