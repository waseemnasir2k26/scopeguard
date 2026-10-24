"""End-to-end analysis: SOW + messages -> findings (+ estimates). Pure; writes nothing.

scopeguard suggests, the human decides: this module never produces change-order drafts.
It sorts items into Possible extras / Needs your call / In scope / set aside."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .acts import apply_withdrawals, content_tokens, free_offer_sentences
from .changeorder import estimate
from .classify import classify_all
from .extract import (EvidenceIndex, consolidate_findings, extract_requests, llm_requests, merge_requests,
                      split_sentences)
from .messages import load_messages
from .models import OUT_OF_SCOPE, Finding, Message, Sow
from .rates import RateCard
from .redact import redact
from .rules import assign_revision_rounds, rounds_log
from .sow import load_sow, parse_sow
from .status import assign_status
from .textutil import normalize

MAX_MESSAGES = 200_000


@dataclass
class Options:
    llm: object = None
    client: list[str] = field(default_factory=list)
    me: list[str] = field(default_factory=list)
    rates: RateCard = field(default_factory=RateCard)
    tone: str = "friendly"
    redact: bool = False
    llm_extract: bool = False
    min_confidence: float = 0.5
    progress: Optional[Callable] = None
    ledger: object = None          # a Ledger: decisions you made earlier (dismissed / billed)


@dataclass
class Result:
    sow: Sow
    messages: list[Message]
    client_messages: list[Message]
    findings: list[Finding]
    warnings: list[str]
    meta: dict


class ClientError(ValueError):
    """The client could not be identified safely."""


def _tokens(name: str) -> list[str]:
    return re.findall(r"[^\W_]+", normalize(name))


def select_client_messages(messages: list[Message], sow_text: str, client: list[str], me: list[str]):
    """Who is the client? Never guessed. --client must match a sender by WHOLE name tokens ("Dana"
    matches "Dana Okafor", "Dan" does not match "Dana"); several matching senders produce a warning."""
    warnings: list[str] = []
    senders = list(dict.fromkeys(m.sender for m in messages))
    if client:
        chosen_senders: list[str] = []
        for entry in client:
            want = _tokens(entry)
            hits = [s for s in senders if want and set(want) <= set(_tokens(s))]
            if not hits:
                raise ClientError(f"--client {entry!r} matches no sender. Senders found: {senders}")
            if len(hits) > 1:
                warnings.append(f"--client {entry!r} matches several senders {hits}; all are treated as the client. "
                                "Use the full name to be exact.")
            chosen_senders += [h for h in hits if h not in chosen_senders]
        return [m for m in messages if m.sender in chosen_senders], warnings
    if me:
        mine = [_tokens(x) for x in me]
        return [m for m in messages if not any(set(t) <= set(_tokens(m.sender)) for t in mine)], warnings
    if len(senders) > 1:
        named = [s for s in senders if len(_tokens(s)) >= 1 and all(
            re.search(r"\b" + re.escape(t) + r"\b", normalize(sow_text)) for t in _tokens(s))]
        extra = (f" The SOW names {named}; one of them is probably you." if named else "")
        raise ClientError(f"This export has several senders {senders}, so scopeguard cannot tell who the client is."
                          f"{extra} Pass --client \"Full Name\" (or --me \"Your Name\").")
    return list(messages), warnings


def _apply_free_offers(findings: list, all_messages: list, client_msgs: list) -> int:
    """A freelancer message that waives the charge ("on the house", "no extra charge", "I'll throw it in")
    marks the item it refers to as INCLUDED-BY-YOU: never billable."""
    client_ids = {m.id for m in client_msgs}
    others = [m for m in all_messages if m.id not in client_ids]
    if not others:
        return 0
    order = {m.id: i for i, m in enumerate(all_messages)}
    n = 0
    for _, m, sent in free_offer_sentences(others, split_sentences):
        toks = content_tokens(sent)
        hit = [f for f in findings if toks & content_tokens(f.request.text())]
        if not hit:                                   # "that one's on the house" refers to the item just before
            before = [f for f in findings if order.get(f.request.message_id, 0) <= order.get(m.id, 0)
                      and abs(order.get(f.request.message_id, 0) - order.get(m.id, 0)) <= 3]
            hit = before[-1:]
        for f in hit:
            if not f.free_by:
                f.free_by = sent
                n += 1
    return n


def analyze(sow: Sow | str | Path, message_paths=None, opts: Optional[Options] = None, *,
            messages: Optional[list[Message]] = None, sow_text: str = "", fmt: str = "auto") -> Result:
    opts = opts or Options()
    t0 = time.time()
    if isinstance(sow, (str, Path)):
        from .sow import load_text
        sow_text = load_text(sow)
        sow = parse_sow(sow_text)
    if messages is None:
        messages = load_messages(message_paths or [], fmt)
    else:
        for i, m in enumerate(messages, 1):
            if not m.id:
                m.id = f"M{i}"
    if len(messages) > MAX_MESSAGES:
        raise ValueError(f"The input has {len(messages):,} messages; the limit is {MAX_MESSAGES:,} per run. "
                         "Split the export (for example by month) and run each part.")
    warnings: list[str] = []
    if not sow.clauses:
        warnings.append("No clauses were found in the SOW; every request will be UNCLEAR.")
    client_msgs, w = select_client_messages(messages, sow_text, opts.client, opts.me)
    warnings += w

    redact_fn = redact if opts.redact else (lambda s: s)
    requests, skipped = extract_requests(client_msgs)
    dropped = 0
    if opts.llm_extract and opts.llm is not None:
        extra, dropped = llm_requests(opts.llm, client_msgs, redact_fn)
        requests = merge_requests(requests, extra)
        order = {m.id: i for i, m in enumerate(client_msgs)}
        requests.sort(key=lambda r: order.get(r.message_id, 0))
        if dropped:
            warnings.append(f"Evidence rule dropped {dropped} LLM-extracted request(s) whose quote was not found verbatim.")
    index = EvidenceIndex(client_msgs)             # built once; lookups do not rescan every message
    kept = []
    for r in requests:
        if index.find(r.quote) is None:
            dropped += 1
        else:
            kept.append(r)
    requests = apply_withdrawals(kept)             # a later "never mind" cancels the earlier item
    for i, r in enumerate(requests, 1):
        r.id = f"Q{i}"
    assign_revision_rounds([r for r in requests if not r.withdrawn_by])
    log = rounds_log(requests)

    if opts.llm is None:
        raise ValueError("Options.llm is required")
    raw = classify_all(requests, sow, opts.llm, redact_fn, opts.min_confidence, opts.progress)
    findings = consolidate_findings(raw, client_msgs, sow)    # same verdict + same clause kind only
    n_before = len(raw)
    n_free = _apply_free_offers(findings, messages, client_msgs)
    decisions = opts.ledger.decisions() if opts.ledger is not None else {}
    for f in findings:
        if f.rule == "revision_limit":
            f.round_log = log
        if f.cls == OUT_OF_SCOPE:
            f.estimate = estimate(f.request.quote, (f.estimate or {}).get("llm_hours"), opts.rates,
                                  use_tasks=f.rule != "revision_limit")
        else:
            f.estimate = None
        assign_status(f, decisions)
    counts: dict = {}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    meta = {"currency": opts.rates.currency, "llm": opts.llm.label(),
            "cloud": bool(getattr(opts.llm, "is_cloud", False)), "redacted": opts.redact,
            "seconds": round(time.time() - t0, 1), "messages": len(messages), "client_messages": len(client_msgs),
            "requests": len(findings), "merged_duplicates": n_before - len(findings),
            "dropped_by_evidence_rule": dropped, "tone": opts.tone, "revision_limit": sow.revision_limit,
            "revision_rounds_used": max((r.revision_round for r in requests), default=0),
            "ledger": str(opts.ledger.path) if opts.ledger is not None else "", "status_counts": counts, "free_offers": n_free, "skipped": skipped[:50], "skipped_total": len(skipped)}
    return Result(sow=sow, messages=messages, client_messages=client_msgs, findings=findings,
                  warnings=warnings, meta=meta)
