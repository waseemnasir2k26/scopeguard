"""End-to-end analysis: SOW + messages -> findings (+ estimates). Pure; writes nothing."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .changeorder import estimate
from .classify import classify_all
from .extract import find_evidence, heuristic_requests, llm_requests, merge_requests
from .messages import load_messages
from .models import OUT_OF_SCOPE, Finding, Message, Sow
from .rates import RateCard
from .redact import redact
from .rules import assign_revision_rounds
from .sow import load_sow, parse_sow
from .textutil import normalize


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


@dataclass
class Result:
    sow: Sow
    messages: list[Message]
    client_messages: list[Message]
    findings: list[Finding]
    warnings: list[str]
    meta: dict


def select_client_messages(messages: list[Message], sow_text: str, client: list[str], me: list[str]):
    warnings: list[str] = []
    senders = list(dict.fromkeys(m.sender for m in messages))
    nclient = [normalize(c) for c in client]
    nme = [normalize(c) for c in me]
    if nclient:
        chosen = [m for m in messages if any(c in normalize(m.sender) for c in nclient)]
        if not chosen:
            warnings.append(f"--client {client} matched no sender. Senders found: {senders}")
        return chosen, warnings
    if nme:
        return [m for m in messages if not any(c in normalize(m.sender) for c in nme)], warnings
    if len(senders) > 1:
        sow_n = normalize(sow_text)
        guess = [s for s in senders if any(len(tok) > 2 and re.search(r"\b" + re.escape(tok) + r"\b", sow_n)
                                           for tok in normalize(s).split())]
        if guess and len(guess) < len(senders):
            warnings.append(f"Treating {guess} as the client (name appears in the SOW). Use --client / --me to override.")
            return [m for m in messages if m.sender in guess], warnings
        warnings.append(f"Could not tell who the client is among {senders}; analysing everyone. "
                        "Pass --client NAME or --me NAME.")
    return list(messages), warnings


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
    warnings: list[str] = []
    if not sow.clauses:
        warnings.append("No clauses were found in the SOW; every request will be UNCLEAR or OUT-OF-SCOPE.")
    client_msgs, w = select_client_messages(messages, sow_text, opts.client, opts.me)
    warnings += w

    redact_fn = redact if opts.redact else (lambda s: s)
    requests = heuristic_requests(client_msgs)
    dropped = 0
    if opts.llm_extract and opts.llm is not None:
        extra, dropped = llm_requests(opts.llm, client_msgs, redact_fn)
        requests = merge_requests(requests, extra)
        order = {m.id: i for i, m in enumerate(client_msgs)}
        requests.sort(key=lambda r: order.get(r.message_id, 0))
        if dropped:
            warnings.append(f"Evidence rule dropped {dropped} LLM-extracted request(s) whose quote was not found verbatim.")
    # evidence rule applies to everything that reaches the report
    kept = []
    for r in requests:
        if find_evidence(r.quote, client_msgs) is None:
            dropped += 1
            continue
        kept.append(r)
    requests = kept
    for i, r in enumerate(requests, 1):
        r.id = f"Q{i}"
    assign_revision_rounds(requests)

    if opts.llm is None:
        raise ValueError("Options.llm is required")
    findings = classify_all(requests, sow, opts.llm, redact_fn, opts.min_confidence, opts.progress)
    for f in findings:
        if f.cls == OUT_OF_SCOPE:
            f.estimate = estimate(f.request.quote, (f.estimate or {}).get("llm_hours"), opts.rates,
                                   use_tasks=f.rule != "revision_limit")
        else:
            f.estimate = None
    meta = {"llm": opts.llm.label(), "cloud": bool(getattr(opts.llm, "is_cloud", False)), "redacted": opts.redact,
            "seconds": round(time.time() - t0, 1), "messages": len(messages), "client_messages": len(client_msgs),
            "requests": len(requests), "dropped_by_evidence_rule": dropped, "tone": opts.tone,
            "revision_limit": sow.revision_limit,
            "revision_rounds_used": max((r.revision_round for r in requests), default=0)}
    return Result(sow=sow, messages=messages, client_messages=client_msgs, findings=findings,
                  warnings=warnings, meta=meta)
