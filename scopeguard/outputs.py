"""Write report.html, summary.md, scope-log.csv and findings.json.

analyze NEVER writes change-order drafts. Drafts come from `scopeguard bill <id>`, on your say-so."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .changeorder import headline, money
from .models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR
from .pipeline import Result
from .rates import RateCard
from .report import render_html
from .status import BILLED, CALL, DISMISSED, EXTRA, FREE, IN, LABELS, SET_ASIDE, WITHDRAWN

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(v) -> str:
    """Neutralise spreadsheet formula injection: prefix risky leading characters with an apostrophe."""
    s = "" if v is None else str(v)
    s = s.replace("\r", " ").replace("\n", " ")
    if s.startswith(_DANGEROUS):
        s = "'" + s
    return s


def write_csv(res: Result, path: Path) -> None:
    cols = ["id", "fingerprint", "status", "class", "confidence", "sender", "date", "quote", "clause_id", "clause_text",
            "rule", "reason", "est_hours", "est_hours_low", "est_hours_high", "est_amount", "est_amount_low",
            "est_amount_high", "currency", "estimate_basis", "draft_file"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in res.findings:
            e = f.estimate or {}
            row = [f.id, f.fingerprint, LABELS.get(f.status, f.status), f.cls, f"{f.confidence:.2f}", f.request.sender,
                   f.request.ts or "", " | ".join(f.request.all_quotes()), f.clause_id or "", f.clause_text,
                   f.rule or "", f.reason,
                   e.get("hours") if e.get("hours") is not None else "", e.get("hours_low") or "", e.get("hours_high") or "",
                   e.get("amount", ""), e.get("amount_low", ""), e.get("amount_high", ""),
                   e.get("currency", ""), e.get("basis", ""), f.draft_file]
            w.writerow([csv_safe(x) for x in row])


def counts(res: Result) -> dict:
    c = {EXTRA: 0, CALL: 0, IN: 0, "set_aside": 0}
    for f in res.findings:
        if f.status in SET_ASIDE:
            c["set_aside"] += 1
        else:
            c[f.status] = c.get(f.status, 0) + 1
    return c


def _quotes(f) -> str:
    return " / ".join(f'"{q}"' for q in f.request.all_quotes())


def write_summary(res: Result, card: RateCard, path: Path) -> None:
    c = counts(res)
    m = res.meta
    L = ["# scopeguard summary", "",
         f"- **{headline(res.findings, card.currency)}**. A rough band from your rate card, not an invoice.",
         f"- Items: **{len(res.findings)}** (possible extras {c[EXTRA]}, needs your call {c[CALL]}, in scope {c[IN]}, "
         f"set aside {c['set_aside']})",
         f"- Model: `{m['llm']}`{' (cloud)' if m['cloud'] else ' (local)'}; redaction {'on' if m['redacted'] else 'off'}; took {m['seconds']} s",
         f"- Messages parsed: {m['messages']}; from the client: {m['client_messages']}"]
    if m["revision_limit"]:
        L.append(f"- Revision rounds used: {m['revision_rounds_used']} of {m['revision_limit']} included")
    if m["dropped_by_evidence_rule"]:
        L.append(f"- Dropped by the evidence rule (quote not found verbatim): {m['dropped_by_evidence_rule']}")
    if m.get("skipped_total"):
        L.append(f"- Skipped on purpose (pleasantries, status questions, billing admin): {m['skipped_total']}")
    for w in res.warnings:
        L.append(f"- Warning: {w}")
    L += ["", "> scopeguard suggests; you decide. Nothing was drafted and nothing was sent. To draft a change order for an",
          "> item you agree with: `scopeguard bill <id>`. To stop seeing one: `scopeguard dismiss <id>`.", ""]
    sections = (("Possible extras", [EXTRA]), ("Needs your call", [CALL]), ("In scope", [IN]),
                ("Set aside (withdrawn, included by you, dismissed, drafted)", list(SET_ASIDE)))
    for title, statuses in sections:
        items = [f for f in res.findings if f.status in statuses]
        L += [f"## {title} ({len(items)})", ""]
        for f in items:
            cite = f.clause_id or "no clause"
            extra = ""
            if f.estimate and f.status == EXTRA:
                e = f.estimate
                extra = (f" Estimate {money(e['amount_low'], e['currency'])} to {money(e['amount_high'], e['currency'])}."
                         f" `scopeguard bill {f.fingerprint}`")
            tag = f" [{LABELS[f.status]}]" if f.status in SET_ASIDE else ""
            L.append(f"- **{f.id}** `{f.fingerprint}` ({f.confidence:.2f}, {cite}){tag} {_quotes(f)} - {f.reason}{extra}")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def write_all(res: Result, out_dir: str | Path, card: RateCard, tone: str = "friendly") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(res, out / "scope-log.csv")
    write_summary(res, card, out / "summary.md")
    (out / "report.html").write_text(render_html(res, card), encoding="utf-8")
    d = to_dict(res)
    d["tone"] = tone
    (out / "findings.json").write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def to_dict(res: Result) -> dict:
    return {
        "meta": res.meta,
        "headline": headline(res.findings, res.meta.get("currency", "USD")),
        "warnings": res.warnings,
        "revision_limit": res.sow.revision_limit,
        "findings": [{
            "id": f.id, "fingerprint": f.fingerprint, "status": f.status, "class": f.cls,
            "confidence": round(f.confidence, 2), "quote": f.request.quote, "also_quoted": f.request.extra_quotes,
            "sender": f.request.sender, "date": f.request.ts, "clause_id": f.clause_id,
            "clause_text": f.clause_text or None, "clause_ref": f.clause_ref or None, "clause_kind": f.clause_kind or None,
            "rule": f.rule, "reason": f.reason, "title": f.title, "estimate": f.estimate,
            "draft_file": f.draft_file or None, "notes": f.notes, "round_log": f.round_log,
            "revision_round": f.request.revision_round, "act": f.request.act,
            "withdrawn_by": f.request.withdrawn_by or None, "free_by": f.free_by or None,
        } for f in res.findings],
    }
