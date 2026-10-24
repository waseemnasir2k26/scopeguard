"""Split client messages into candidate requests.

Heuristics first (always on). An LLM pass is optional; anything an LLM proposes must
quote the messages verbatim, otherwise it is dropped (the evidence rule).

Each candidate also gets an *act*: a real request, or a retraction ("changed my mind") or a
hypothetical / price question ("just curious"). Only real requests can ever be billed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterable, Optional

from .models import Message, Request
from .acts import classify_act
from .textutil import concepts, normalize

_IMPERATIVE = (r"^(?:(?:also|and|then|plus|so|ok|okay|sorry|actually)[,!.]?\s+)*(?:please\s+)?"
               r"(?:add|make|change|move|swap|fix|update|install|create|build|set ?up|connect|put|use|place|"
               r"include|send|write|translate|remove|replace|adjust|tweak|rename|resize|redo|try|show|embed|link|"
               r"design|shoot|publish|upload|integrate|schedule|post|record|drop)\b")
_CUES = re.compile(
    r"\b(can|could|would|will) (you|we|u|the|this|that|it|our|my|there)\b|\bplease\b|\bpls\b|"
    r"\bwe (need|want|would like|'d like)\b|\bi (need|want|would like|'d like|wanna)\b|\bi'?d like\b|"
    r"\bwe'?d (?:love|like)\b|\bwould (?:love|like)\b|\bdon'?t forget\b|\bhoping\b|"
    r"\b(also|additionally|one more thing|by the way|btw|while you'?re at it|on top of that)\b|"
    r"\bit would be (nice|great|good|helpful)\b|\bwhat about\b|\bhow about\b|\blet'?s (add|have|do|try)\b|"
    r"\bmake (it|the|sure|them|this)\b|\bchange\b|\bneeds?\b|\bwants?\b|\bswap\b|\breplace\b|\bmove\b|"
    + _IMPERATIVE.replace("^", r"(?:^|[.!?]\s+)"), re.I)
_SKIP = re.compile(
    r"^(thanks|thank you|thx|ok(ay)?|great|perfect|nice|cool|sounds good|looks (great|good)|love it|got it|"
    r"yes|yep|no problem|hi|hello|hey|good (morning|evening)|will do)\b[^?]*$", re.I)
_STATUS = re.compile(r"\b(when will|any update|how long|what'?s the (status|eta)|did you (get|receive|see)|are you (there|free|available))\b", re.I)
_ADMIN = re.compile(r"\b(invoice|invoices|receipt|bank details|payment schedule)\b", re.I)   # billing admin, not scope

_REV_CUE = re.compile(
    r"\b(change|changing|tweak|adjust|revise|modify|redo|rework|swap|replace|recolou?r|rename|reposition|resize|"
    r"move|bigger|smaller|larger|darker|lighter|brighter|font|colou?r|instead of|different (colou?r|font|image|photo|layout|"
    r"order)|order of|typo|typos|fix|correct|spelling|reword|shorten|lengthen|spacing|padding|margin|align(ed|ment)?|"
    r"position|reorder|rearrange|enlarge|shrink|text size|headline|heading|increase|decrease|reduce|raise|lower|"
    r"widen|narrow|taller|shorter|wider|thicker|thinner|bolder)\b", re.I)
_NEW_CUE = re.compile(
    r"\b(add|adding|new|build|create|set ?up|integrate|connect|write|translate|install|launch|start|include|"
    r"also (want|need)|plus)\b", re.I)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])|\n+")
_WORD = re.compile(r"\w+")


def is_revision(text: str) -> bool:
    return bool(_REV_CUE.search(text)) and not _NEW_CUE.search(text)


def split_sentences(text: str) -> list[str]:
    out = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip()
        if part:
            out.append(part)
    return out


def extract_requests(messages: Iterable[Message]) -> tuple[list[Request], list[dict]]:
    """Candidate requests plus the sentences that were skipped on purpose (never silently):
    pleasantries, status questions and billing admin."""
    out: list[Request] = []
    skipped: list[dict] = []
    for m in messages:
        for sent in split_sentences(m.text):
            act = classify_act(sent)
            why = ("pleasantry" if (_SKIP.match(sent) and len(sent.split()) <= 6) else "status question" if _STATUS.search(sent)
                   else "billing admin" if _ADMIN.search(sent) else "")
            if why:
                if _CUES.search(sent) or act != "request":
                    skipped.append({"quote": sent, "why": why, "message": m.id})
                continue
            if len(sent.split()) < 2:
                continue
            # a retraction, hedge or hypothetical is kept (as UNCLEAR, never billed) even without a request cue
            if act == "request" and not _CUES.search(sent):
                continue
            out.append(Request(id="", message_id=m.id, sender=m.sender, ts=m.ts, quote=sent,
                               is_revision=is_revision(sent) and act == "request", act=act))
    return out, skipped


def heuristic_requests(messages: Iterable[Message]) -> list[Request]:
    return extract_requests(messages)[0]


# ---------------------------------------------------------------- evidence rule (indexed)
class EvidenceIndex:
    """Evidence rule: a quote must appear verbatim (modulo case/whitespace/quote style).

    Built once per run: an inverted index word -> messages. A lookup intersects the posting lists of
    the quote's interior words (each is a whole word in any true match) and only then does the
    substring check, so 50k messages cost milliseconds per quote, not a full rescan."""

    def __init__(self, messages: Iterable[Message]):
        self.msgs = list(messages)
        self.norm = [normalize(m.text) for m in self.msgs]
        self.index: dict[str, list[int]] = {}
        self._cache: dict[str, Optional[int]] = {}
        for i, t in enumerate(self.norm):
            for w in set(_WORD.findall(t)):
                self.index.setdefault(w, []).append(i)

    def find(self, quote: str) -> Optional[Message]:
        nq = normalize(quote)
        if len(nq) < 8:
            return None
        if nq in self._cache:
            i = self._cache[nq]
            return None if i is None else self.msgs[i]
        i = self._lookup(nq)
        self._cache[nq] = i
        return None if i is None else self.msgs[i]

    def _lookup(self, nq: str) -> Optional[int]:
        words = _WORD.findall(nq)
        interior = set(words[1:-1])
        if interior:
            best = None
            for w in interior:
                p = self.index.get(w)
                if p is None:
                    return None            # a whole word of the quote is in no message at all
                if best is None or len(p) < len(best):
                    best = p
            order = best                   # scan only the rarest word's messages
        else:
            order = range(len(self.msgs))
        for i in order:
            if nq in self.norm[i]:
                return i
        return None


def find_evidence(quote: str, messages: Iterable[Message]) -> Optional[Message]:
    return EvidenceIndex(messages).find(quote)


EXTRACT_SYSTEM = (
    "You extract client REQUESTS from chat messages for a freelancer. A request is something the client asks "
    "the freelancer to do, add, change or deliver. Ignore thanks, greetings, status questions and the "
    "freelancer's own words. Quote each request VERBATIM from the message text (copy exactly; do not "
    "paraphrase or merge messages). Reply with JSON only: "
    '{"requests":[{"message_id":"M1","quote":"..."}]}'
)


def _original_span(quote: str, original: str, redact_fn) -> Optional[str]:
    for sent in split_sentences(original):
        if normalize(quote) in normalize(redact_fn(sent)):
            return sent
    return None


def llm_requests(llm, messages: list[Message], redact_fn=lambda s: s, batch: int = 12) -> tuple[list[Request], int]:
    """Ask the LLM for requests. Returns (requests, number dropped by the evidence rule)."""
    out: list[Request] = []
    dropped = 0
    for i in range(0, len(messages), batch):
        chunk = messages[i:i + batch]
        payload = {"messages": [{"message_id": m.id, "sender": m.sender, "text": redact_fn(m.text)} for m in chunk]}
        user = "Extract the requests.\nEXTRACT_JSON: " + json.dumps(payload, ensure_ascii=False)
        data = llm.json_call(EXTRACT_SYSTEM, user)
        items = data.get("requests") if isinstance(data, dict) else None
        for it in items or []:
            if not isinstance(it, dict) or not isinstance(it.get("quote"), str):
                dropped += 1
                continue
            nq = normalize(it["quote"])
            msg = None
            if len(nq) >= 8:
                cand = [m for m in chunk if m.id == it.get("message_id")] or chunk
                msg = next((m for m in cand if nq in normalize(redact_fn(m.text))), None)
            if msg is None:
                dropped += 1
                continue
            quote = it["quote"].strip()
            if redact_fn(msg.text) != msg.text:   # recover the un-redacted wording for the report
                quote = _original_span(quote, msg.text, redact_fn) or quote
            act = classify_act(quote)
            out.append(Request(id="", message_id=msg.id, sender=msg.sender, ts=msg.ts, quote=quote,
                               is_revision=is_revision(quote) and act == "request", act=act))
    return out, dropped


def merge_requests(primary: list[Request], extra: list[Request]) -> list[Request]:
    merged = list(primary)
    for r in extra:
        nr = normalize(r.quote)
        if any(p.message_id == r.message_id and (nr in normalize(p.quote) or normalize(p.quote) in nr) for p in merged):
            continue
        merged.append(r)
    return merged


# ---------------------------------------------------------------- consolidation (after classification)
_ANAPHOR = re.compile(r"\b(it|that|this|them|those|these)\b", re.I)
WINDOW_DAYS = 7
WINDOW_MESSAGES = 8


def _toks(r: Request) -> set:
    return set(concepts(r.quote))


def _same_intent(a: Request, b: Request, order: dict) -> bool:
    """a is an earlier consolidated item, b a later request from the same sender."""
    if a.sender != b.sender:
        return False
    if a.ts and b.ts:   # seven days when both have timestamps, else eight messages
        try:
            if abs((datetime.fromisoformat(b.ts) - datetime.fromisoformat(a.ts)).total_seconds()) > WINDOW_DAYS * 86400:
                return False
        except ValueError:
            pass
    elif abs(order.get(b.message_id, 0) - order.get(a.message_ids[-1] if a.message_ids else a.message_id, 0)) > WINDOW_MESSAGES:
        return False
    same_msg = a.message_id == b.message_id or (a.message_ids and a.message_ids[-1] == b.message_id)
    if same_msg and len(b.quote.split()) <= 16 and _ANAPHOR.search(b.quote) and not b.is_revision:
        return True   # a short follow-up that refers back ("set it up and write the first posts")
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return False
    jac = len(ta & tb) / len(ta | tb)
    if a.is_revision or b.is_revision:
        return a.is_revision and b.is_revision and jac >= 0.6
    strong = {t for t in ta & tb if t.startswith("c:")}
    return jac >= 0.5 or bool(strong)


def consolidate_findings(findings: list, messages: list, sow) -> list:
    """Merge split and near-duplicate requests into one item carrying several quotes, so each real
    ask is priced once. Only items with the SAME verdict and the same cited-clause kind are merged;
    an IN and an OUT item are never combined, and retractions/hypotheticals never merge."""
    order = {m.id: i for i, m in enumerate(messages)}

    def kind(f):
        c = sow.by_id(f.clause_id) if f.clause_id else None
        return c.kind if c else None

    kept: list = []
    for f in findings:
        target = None
        if f.request.act == "request":
            for k in reversed(kept[-WINDOW_MESSAGES:]):
                if (k.request.act == "request" and k.cls == f.cls and kind(k) == kind(f)
                        and bool(k.request.withdrawn_by) == bool(f.request.withdrawn_by)
                        and _same_intent(k.request, f.request, order)):
                    target = k
                    break
        if target is None:
            f.request.message_ids = [f.request.message_id]
            kept.append(f)
        else:
            target.request.extra_quotes.append(f.request.quote)
            target.request.message_ids.append(f.request.message_id)
            target.notes.append("Merged a split or duplicate request into this item.")
    for i, f in enumerate(kept, 1):
        f.id = f"REQ-{i:02d}"
    return kept
