"""Write report.html, change-orders/*.md, scope-log.csv, summary.md and findings.json."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .changeorder import draft, draftable, headline, money
from .models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR
from .pipeline import Result
from .rates import RateCard
from .report import render_html
from .textutil import slug

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(v) -> str:
    """Neutralise spreadsheet formula injection: prefix risky leading characters with an apostrophe."""
    s = "" if v is None else str(v)
    s = s.replace("\r", " ").replace("\n", " ")
    if s.startswith(_DANGEROUS):
        s = "'" + s
    return s


def write_csv(res: Result, path: Path) -> None:
    cols = ["id", "class", "confidence", "sender", "date", "quote", "clause_id", "clause_text", "rule", "reason",
            "est_hours", "est_hours_low", "est_hours_high", "est_amount", "est_amount_low", "est_amount_high", "currency", "estimate_basis", "change_order_file"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in res.findings:
            e = f.estimate or {}
            row = [f.id, f.cls, f"{f.confidence:.2f}", f.request.sender, f.request.ts or "", " | ".join(f.request.all_quotes()),
                   f.clause_id or "", f.clause_text, f.rule or "", f.reason,
                   e.get("hours") if e.get("hours") is not None else "", e.get("hours_low") or "", e.get("hours_high") or "",
                   e.get("amount", ""), e.get("amount_low", ""), e.get("amount_high", ""),
                   e.get("currency", ""), e.get("basis", ""), f.draft_file]
            w.writerow([csv_safe(x) for x in row])


def counts(res: Result) -> dict:
    c = {IN_SCOPE: 0, OUT_OF_SCOPE: 0, UNCLEAR: 0}
    for f in res.findings:
        c[f.cls] += 1
    return c


def write_summary(res: Result, card: RateCard, path: Path) -> None:
    c = counts(res)
    m = res.meta
    L = ["# scopeguard summary", "",
         f"- Requests found: **{len(res.findings)}** (in-scope {c[IN_SCOPE]}, out-of-scope {c[OUT_OF_SCOPE]}, unclear {c[UNCLEAR]})",
         f"- **{headline(res.findings, card.currency)}**. A rough band from your rate card, not an invoice.",
         f"- Model: `{m['llm']}`{' (cloud)' if m['cloud'] else ' (local)'}; redaction {'on' if m['redacted'] else 'off'}; took {m['seconds']} s",
         f"- Messages parsed: {m['messages']}; from the client: {m['client_messages']}"]
    if m["revision_limit"]:
        L.append(f"- Revision rounds used: {m['revision_rounds_used']} of {m['revision_limit']} included")
    if m["dropped_by_evidence_rule"]:
        L.append(f"- Dropped by the evidence rule (quote not found verbatim): {m['dropped_by_evidence_rule']}")
    for w in res.warnings:
        L.append(f"- Warning: {w}")
    L += ["", "> LLM judgement is a draft. You decide what to raise with the client. Nothing was sent.", ""]
    for title, cls in (("Out of scope", OUT_OF_SCOPE), ("Unclear", UNCLEAR), ("In scope", IN_SCOPE)):
        items = [f for f in res.findings if f.cls == cls]
        L.append(f"## {title} ({len(items)})")
        L.append("")
        for f in items:
            cite = f.clause_id or "no matching clause"
            extra = ""
            if f.estimate:
                e = f.estimate
                extra = f" Est. {money(e['amount'], e['currency'])}."
            quotes = " / ".join(f'"{q}"' for q in f.request.all_quotes())
            L.append(f"- **{f.id}** ({f.confidence:.2f}, {cite}) {quotes} - {f.reason}{extra}")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def write_all(res: Result, out_dir: str | Path, card: RateCard, tone: str = "friendly") -> Path:
    out = Path(out_dir)
    co_dir = out / "change-orders"
    co_dir.mkdir(parents=True, exist_ok=True)
    for old in co_dir.glob("*.md"):   # keep the folder in sync with this run
        old.unlink()
    for i, f in enumerate([x for x in res.findings if draftable(x)], 1):
        name = f"{i:02d}-{f.id.lower()}-{slug(f.title)}.md"
        (co_dir / name).write_text(draft(f, card, tone, res.sow.revision_limit), encoding="utf-8")
        f.draft_file = f"change-orders/{name}"
    write_csv(res, out / "scope-log.csv")
    write_summary(res, card, out / "summary.md")
    (out / "report.html").write_text(render_html(res, card), encoding="utf-8")
    (out / "findings.json").write_text(json.dumps(to_dict(res), indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def to_dict(res: Result) -> dict:
    return {
        "meta": res.meta,
        "headline": headline(res.findings, res.meta.get("currency", "USD")),
        "warnings": res.warnings,
        "findings": [{
            "id": f.id, "class": f.cls, "confidence": round(f.confidence, 2), "quote": f.request.quote, "also_quoted": f.request.extra_quotes,
            "sender": f.request.sender, "date": f.request.ts, "clause_id": f.clause_id,
            "clause_text": f.clause_text or None, "rule": f.rule, "reason": f.reason, "title": f.title,
            "estimate": f.estimate, "change_order_file": f.draft_file or None, "notes": f.notes,
        } for f in res.findings],
    }
