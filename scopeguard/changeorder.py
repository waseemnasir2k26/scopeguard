"""Change-order maths and polite draft text. Drafts are files; nothing here sends anything."""
from __future__ import annotations

import math
import re
from typing import Optional

from .models import Finding
from .rates import RateCard
from .textutil import concepts

SYMBOLS = {"USD": "$", "EUR": "EUR ", "GBP": "GBP ", "AUD": "A$", "CAD": "C$"}


def money(amount: float, currency: str) -> str:
    sym = SYMBOLS.get(currency, currency + " ")
    return f"{sym}{amount:,.2f}"


def _round_up_quarter(h: float) -> float:
    return math.ceil(h * 4 - 1e-9) / 4


def estimate(quote: str, llm_hours: Optional[float], card: RateCard, use_tasks: bool = True) -> dict:
    """Rate card x hours. Always flagged as an estimate."""
    low = quote.lower()
    words = set(re.findall(r"[a-z0-9]+", low)) | {k[2:] for k in concepts(quote) if k.startswith("c:")}
    task = None if not use_tasks else next((t for t in card.tasks if any(k in words or k in low for k in t.match)), None)
    base = dict(currency=card.currency, rate=card.hourly_rate, is_estimate=True, placeholder_rate=card.placeholder)
    if task and task.fixed is not None:
        amt = round(task.fixed, 2)
        return dict(base, hours=None, hours_low=None, hours_high=None, amount=amt, amount_low=amt, amount_high=amt,
                    basis=f"rate card: {task.label} (fixed)")
    if task and task.hours is not None:
        hours, basis = task.hours, f"rate card: {task.label}"
    elif isinstance(llm_hours, (int, float)) and not isinstance(llm_hours, bool) and 0 < llm_hours <= 200:
        hours, basis = float(llm_hours), "LLM-proposed hours x hourly rate"
    else:
        hours, basis = card.default_hours, "default hours x hourly rate"
    hours = _round_up_quarter(max(hours, card.minimum_hours))
    # An honest spread instead of false precision: 0.75x to 1.5x of the point estimate
    # (an arbitrary but stated band; a rate-card task is your own number, so it gets the same band).
    lo = max(card.minimum_hours, math.floor(hours * 0.75 * 4) / 4)
    hi = _round_up_quarter(hours * 1.5)
    rate = card.hourly_rate
    return dict(base, hours=hours, hours_low=lo, hours_high=hi, amount=round(hours * rate, 2),
                amount_low=round(lo * rate, 2), amount_high=round(hi * rate, 2), basis=basis)


def _first_name(sender: str) -> str:
    s = sender.strip()
    return s.split()[0] if s and s.lower() not in {"client", "unknown"} else "there"


_TONES = {
    "friendly": {
        "open": "Thanks for this, and happy to help.",
        "mid": "This one sits outside what we agreed in the project scope, so I'd like to handle it as a small add-on. That keeps the original timeline and budget safe.",
        "rev": "We've now used the {n} revision round(s) included in the agreement, so I'd like to treat further changes as a small add-on.",
        "close": "If that works for you, just reply \"approved\" and I'll slot it in. Happy to talk it through if you'd like.",
    },
    "neutral": {
        "open": "Thank you for the request.",
        "mid": "This is not covered by the agreed scope, so I'm proposing it as a change order.",
        "rev": "The {n} revision round(s) in the agreement have been used, so further changes would be a change order.",
        "close": "Please confirm if you'd like to proceed and I will schedule the work.",
    },
    "firm": {
        "open": "Thank you for your message.",
        "mid": "This request falls outside the agreed scope and will be handled as a billable change order.",
        "rev": "The {n} revision round(s) in the agreement have been used. Further changes are billable.",
        "close": "Work on this item will start once you approve the estimate in writing.",
    },
}


def draft(f: Finding, card: RateCard, tone: str = "friendly", revision_limit: int | None = None) -> str:
    t = _TONES.get(tone, _TONES["friendly"])
    est = f.estimate or {}
    cur = est.get("currency", card.currency)
    cite = (f"{f.clause_id}" + (f" (SOW {f.clause_ref})" if f.clause_ref else "")) if f.clause_id else "no matching clause"
    mid = t["rev"].format(n=revision_limit or "") if f.rule == "revision_limit" else t["mid"]
    if est.get("hours") is not None:
        calc = (f"{est['hours']:g} h x {money(est['rate'], cur)}/h = {money(est['amount'], cur)} "
                f"(plausible range {est['hours_low']:g}-{est['hours_high']:g} h, "
                f"{money(est['amount_low'], cur)}-{money(est['amount_high'], cur)})")
    else:
        calc = f"{money(est.get('amount', 0), cur)} (fixed price from rate card)"
    lines = [
        "> DRAFT. scopeguard never sends anything. Review, edit the estimate, then send it yourself.",
        "",
        f"**Subject:** Quick scope check: {f.title or 'your request'}",
        "",
        f"Hi {_first_name(f.request.sender)},",
        "",
        f"{t['open']} You asked: " + " / ".join(f'"{q}"' for q in f.request.all_quotes()),
        "",
        f"{mid} " + (f"The closest part of the agreed scope is: {cite}." if f.clause_id
                     else "I couldn't find anything in the agreed scope that covers it."),
        "",
        f"**Estimate:** {calc}",
        "",
        f"This is an estimate based on my rate card; I'll confirm the final figure before starting. {t['close']}",
        "",
        "Best,",
        "[Your name]",
        "",
        "---",
        f"_Basis: {est.get('basis', 'n/a')}. Classified {f.cls} with confidence {f.confidence:.2f}. "
        f"Reason: {f.reason}_",
    ]
    if est.get("placeholder_rate"):
        lines.append("_Warning: no rates.yaml was supplied, so a placeholder hourly rate was used. Edit the figure._")
    return "\n".join(lines) + "\n"


def extra_work(findings) -> dict:
    """Totals over OUT-OF-SCOPE items that cite a clause. Each consolidated item counts once."""
    items = [f for f in findings if f.cls == "OUT-OF-SCOPE" and f.estimate and f.clause_id]
    hl = sum(f.estimate["hours_low"] for f in items if f.estimate.get("hours") is not None)
    hh = sum(f.estimate["hours_high"] for f in items if f.estimate.get("hours") is not None)
    return {"n": len(items), "amount_low": round(sum(f.estimate["amount_low"] for f in items), 2),
            "amount_high": round(sum(f.estimate["amount_high"] for f in items), 2),
            "amount": round(sum(f.estimate["amount"] for f in items), 2),
            "hours_low": hl, "hours_high": hh,
            "fixed": sum(1 for f in items if f.estimate.get("hours") is None)}


def headline(findings, currency: str) -> str:
    t = extra_work(findings)
    if not t["n"]:
        return "No out-of-scope items with a cited clause were found."
    extra = f", plus {t['fixed']} fixed-price" if t["fixed"] else ""
    return (f"Estimated extra work (review before sending): {money(t['amount_low'], currency)} to "
            f"{money(t['amount_high'], currency)} across {t['n']} items ({t['hours_low']:g}-{t['hours_high']:g} h{extra})")
