"""Split client messages into candidate requests.

Heuristics first (always on). An LLM pass is optional; anything an LLM proposes must
quote the messages verbatim, otherwise it is dropped (the evidence rule).
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from .models import Message, Request
from .textutil import normalize

_CUES = re.compile(
    r"\b(can|could|would|will) (you|we|u|the|this|that|it|our|my|there)\b|\bplease\b|\bpls\b|\bwe (need|want|would like|'d like|should)\b|"
    r"\bi (need|want|would like|'d like|wanna)\b|\bi'?d like\b|\b(also|additionally|one more thing|by the way|btw|"
    r"while you'?re at it|on top of that)\b|\bit would be (nice|great|good|helpful)\b|\bcan we\b|\bwhat about\b|"
    r"\bhow about\b|\blet'?s (add|have|do|try)\b|\badd(ing)?\b|\bmake (it|the|sure|them|this)\b|\bchange\b|"
    r"\bneed(s)?\b|\bwant(s)?\b|\binclude\b|\bupdate\b|\binstead\b|\bswap\b|\breplace\b|\bmove\b|\bshould\b|"
    r"\bwould love\b|\bwish\b|\btry\b", re.I)
_SKIP = re.compile(
    r"^(thanks|thank you|thx|ok(ay)?|great|perfect|nice|cool|sounds good|looks (great|good)|love it|got it|"
    r"yes|yep|no problem|hi|hello|hey|good (morning|evening)|will do)\b[^?]*$", re.I)
_STATUS = re.compile(r"\b(when will|any update|how long|what'?s the (status|eta)|did you (get|receive|see)|are you (there|free|available))\b", re.I)
_ADMIN = re.compile(r"\b(invoice|invoices|receipt|bank details|payment schedule)\b", re.I)   # billing admin, not scope
_REV_CUE = re.compile(
    r"\b(change|changing|tweak|adjust|revise|modify|redo|rework|swap|replace|recolou?r|rename|reposition|resize|"
    r"move|bigger|smaller|larger|darker|lighter|brighter|font|colou?r|instead of|different (colou?r|font|image|photo|layout|"
    r"order)|order of|typo|typos|fix|correct|spelling|reword|shorten|lengthen|spacing|padding|margin|align(ed|ment)?|"
    r"position|reorder|rearrange|enlarge|shrink|text size|headline|heading)\b", re.I)
_NEW_CUE = re.compile(
    r"\b(add|adding|new|build|create|set ?up|integrate|connect|write|translate|install|launch|start|include|"
    r"also (want|need)|plus)\b", re.I)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])|\n+")


def is_revision(text: str) -> bool:
    return bool(_REV_CUE.search(text)) and not _NEW_CUE.search(text)


def split_sentences(text: str) -> list[str]:
    out = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip()
        if part:
            out.append(part)
    return out


def heuristic_requests(messages: Iterable[Message]) -> list[Request]:
    out: list[Request] = []
    for m in messages:
        for sent in split_sentences(m.text):
            if len(sent.split()) < 4 or _SKIP.match(sent) or _STATUS.search(sent) or _ADMIN.search(sent):
                continue
            if not _CUES.search(sent):
                continue
            out.append(Request(id="", message_id=m.id, sender=m.sender, ts=m.ts, quote=sent,
                               is_revision=is_revision(sent)))
    return out


def find_evidence(quote: str, messages: Iterable[Message]) -> Optional[Message]:
    """Evidence rule: the quote must appear verbatim (modulo case/whitespace/quote style)."""
    nq = normalize(quote)
    if len(nq) < 8:
        return None
    for m in messages:
        if nq in normalize(m.text):
            return m
    return None


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
            out.append(Request(id="", message_id=msg.id, sender=msg.sender, ts=msg.ts, quote=quote,
                               is_revision=is_revision(quote)))
    return out, dropped


def merge_requests(primary: list[Request], extra: list[Request]) -> list[Request]:
    merged = list(primary)
    for r in extra:
        nr = normalize(r.quote)
        if any(p.message_id == r.message_id and (nr in normalize(p.quote) or normalize(p.quote) in nr) for p in merged):
            continue
        merged.append(r)
    return merged


# ---------------------------------------------------------------- consolidation
_ANAPHOR = re.compile(r"\b(it|that|this|them|those|these)\b", re.I)
WINDOW_DAYS = 7
WINDOW_MESSAGES = 8


def _toks(r: Request) -> set:
    from .textutil import concepts
    return set(concepts(r.quote))


def _same_intent(a: Request, b: Request, order: dict) -> bool:
    """a is an earlier consolidated item, b a later request from the same sender."""
    from datetime import datetime
    if a.sender != b.sender:
        return False
    # window: seven days when both have timestamps, else eight messages
    if a.ts and b.ts:
        try:
            if abs((datetime.fromisoformat(b.ts) - datetime.fromisoformat(a.ts)).total_seconds()) > WINDOW_DAYS * 86400:
                return False
        except ValueError:
            pass
    elif abs(order.get(b.message_id, 0) - order.get(a.message_ids[-1] if a.message_ids else a.message_id, 0)) > WINDOW_MESSAGES:
        return False
    # split request: a short follow-up sentence that refers back ("set it up and write the first posts")
    same_msg = a.message_id == b.message_id or a.message_ids and a.message_ids[-1] == b.message_id
    if same_msg and len(b.quote.split()) <= 16 and _ANAPHOR.search(b.quote) and not b.is_revision:
        return True
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return False
    jac = len(ta & tb) / len(ta | tb)
    if a.is_revision or b.is_revision:
        return a.is_revision and b.is_revision and jac >= 0.6
    strong = {t for t in ta & tb if t.startswith("c:")}
    return jac >= 0.5 or bool(strong)


def consolidate(requests: list, messages: list) -> list:
    """Merge split and near-duplicate requests (same intent, same sender, inside a window) into one
    item carrying several quotes, so each real ask is classified and priced once."""
    order = {m.id: i for i, m in enumerate(messages)}
    kept: list = []
    for r in requests:
        target = None
        for k in reversed(kept[-WINDOW_MESSAGES:]):
            if _same_intent(k, r, order):
                target = k
                break
        if target is None:
            r.message_ids = [r.message_id]
            kept.append(r)
        else:
            target.extra_quotes.append(r.quote)
            target.message_ids.append(r.message_id)
    return kept
