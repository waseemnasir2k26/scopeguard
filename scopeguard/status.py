"""Where each item lands: Possible extras, Needs your call, In scope, or set aside."""
from __future__ import annotations

from .models import IN_SCOPE, OUT_OF_SCOPE, Finding

EXTRA, CALL, IN, WITHDRAWN, FREE, DISMISSED, BILLED = (
    "extra", "call", "in", "withdrawn", "included_by_you", "dismissed", "billed")
SET_ASIDE = (WITHDRAWN, FREE, DISMISSED, BILLED)
STRONG_RULES = {"explicit_exclusion", "numeric_cap", "page_count", "revision_limit"}
MIN_EXTRA_CONFIDENCE = 0.75
LABELS = {EXTRA: "Possible extra", CALL: "Needs your call", IN: "In scope", WITHDRAWN: "Withdrawn by the client",
          FREE: "Included by you", DISMISSED: "Dismissed by you", BILLED: "Drafted by you"}


def assign_status(f: Finding, decisions: dict | None = None) -> str:
    decisions = decisions or {}
    if f.request.withdrawn_by:
        status = WITHDRAWN
    elif f.free_by:
        status = FREE
    elif f.fingerprint in decisions:
        status = DISMISSED if decisions[f.fingerprint].get("decision") == "dismissed" else BILLED
    elif (f.cls == OUT_OF_SCOPE and f.clause_id and f.confidence >= MIN_EXTRA_CONFIDENCE
          and f.request.act == "request" and f.estimate is not None):
        status = EXTRA
    elif f.cls == IN_SCOPE:
        status = IN
    else:
        status = CALL
    f.status = status
    return status


def withdrawn_after_draft_text(f: Finding) -> str:
    return (f"Withdrawn after a change order was drafted on {f.billed_on}: check before sending "
            f"{f.draft_file or 'the draft'}")


def billed_then_withdrawn(findings: list) -> list:
    """Items you already drafted a change order for, which the client has since withdrawn: check before sending."""
    return [f for f in findings if f.billed_on]


def is_extra(f: Finding) -> bool:
    return f.status == EXTRA


def is_strong(f: Finding) -> bool:
    """Strong candidates: decided by a deterministic rule, or by the model at 0.85+ confidence."""
    return f.status == EXTRA and (f.rule in STRONG_RULES or f.confidence >= 0.85)
