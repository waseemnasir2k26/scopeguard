"""`scopeguard bill` and `scopeguard dismiss`: the human decides, item by item.

analyze only suggests. A change-order draft is written here, for the items YOU name (or, with
--all-strong, after you answer y to a listing). Decisions are recorded in the ledger."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .changeorder import draft
from .classify import short_title  # noqa: F401  (re-exported for callers)
from .fsutil import atomic_write
from .ledger import Ledger
from .models import OUT_OF_SCOPE, Finding, Request
from .rates import RateCard
from .status import BILLED, CALL, DISMISSED, EXTRA, is_strong
from .textutil import slug


class BillError(ValueError):
    pass


def load_findings(out_dir: str | Path) -> tuple[dict, list[Finding]]:
    path = Path(out_dir) / "findings.json"
    if not path.exists():
        raise BillError(f"{path} not found. Run `scopeguard analyze --out {out_dir}` first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data, [from_dict(d) for d in data["findings"]]


def from_dict(d: dict) -> Finding:
    req = Request(id=d["id"], message_id="", sender=d.get("sender") or "", ts=d.get("date"), quote=d["quote"],
                  extra_quotes=list(d.get("also_quoted") or []), revision_round=d.get("revision_round") or 0,
                  act=d.get("act") or "request", withdrawn_by=d.get("withdrawn_by") or "")
    f = Finding(id=d["id"], request=req, cls=d["class"], confidence=d.get("confidence", 0.0),
                clause_id=d.get("clause_id"), clause_text=d.get("clause_text") or "", clause_ref=d.get("clause_ref") or "",
                clause_kind=d.get("clause_kind") or "", reason=d.get("reason") or "", title=d.get("title") or "",
                rule=d.get("rule"), estimate=d.get("estimate"), draft_file=d.get("draft_file") or "",
                notes=list(d.get("notes") or []), round_log=list(d.get("round_log") or []),
                status=d.get("status") or "", fingerprint=d.get("fingerprint") or "", free_by=d.get("free_by") or "",
                later_mention=d.get("later_mention") or "", latest_related=d.get("latest_related") or "",
                repeat_of=d.get("repeat_of") or "")
    return f


def ledger_for(data: dict, out_dir: str | Path) -> Ledger:
    """The ledger recorded in findings.json, but only if it lives inside the output folder: a doctored
    findings.json must not point `bill` at some other file."""
    out = Path(out_dir).resolve()
    declared = (data.get("meta") or {}).get("ledger")
    path = Path(declared).resolve() if declared else out / "ledger.json"
    if out != path.parent and out not in path.parents:
        raise BillError(f"findings.json names a ledger outside the output folder ({path}); refusing to use it.")
    return Ledger(path)


def _save(out: Path, data: dict, ledger: Ledger) -> None:
    with ledger.lock():
        atomic_write(out / "findings.json", json.dumps(data, indent=2, ensure_ascii=False))


def select(findings: list[Finding], ids: list[str]) -> list[Finding]:
    """Items by fingerprint (stable across runs) or by REQ-nn (as shown in the last report)."""
    chosen = []
    for raw in ids:
        key = raw.strip().lower()
        hits = [f for f in findings if f.fingerprint.lower() == key or f.id.lower() == key]
        if not hits:
            raise BillError(f"No item {raw!r} in the last analysis. Items: {', '.join(f.id + ' ' + f.fingerprint for f in findings)}")
        chosen.append(hits[0])
    return chosen


def held_back_repeats(findings: list[Finding]) -> list[Finding]:
    """Items held out of Possible extras because they read as a repeat of something you already decided."""
    return [f for f in findings if f.repeat_of and f.status == CALL]


def bill(out_dir: str | Path, ids: list[str], card: RateCard, tone: str = "friendly", all_strong: bool = False,
         confirm: Callable[[list[Finding]], bool] | None = None, repeat_ok: bool = False) -> list[Path]:
    """Write drafts for the chosen items. Refuses anything that is not a Possible extra, except a repeat
    held by the repeat check, which is drafted only when you name it AND pass repeat_ok (--repeat-ok)."""
    data, findings = load_findings(out_dir)
    if all_strong:
        chosen = [f for f in findings if is_strong(f)]
        if not chosen:
            raise BillError("No strong possible extras to draft.")
        if confirm is None or not confirm(chosen):
            raise BillError("Nothing drafted (you did not confirm).")
    else:
        if not ids:
            raise BillError("Name the item(s) to draft, for example: scopeguard bill 3f2a9c1d, or use --all-strong.")
        chosen = select(findings, ids)
    for f in chosen:
        if f.repeat_of and f.status == CALL:
            if not repeat_ok:
                raise BillError(f"{f.id} {f.fingerprint} {f.repeat_of}. It looks like a repeat, so scopeguard will not "
                                "draft it. If it really is a new request, run again with --repeat-ok.")
            if f.cls != OUT_OF_SCOPE or not f.clause_id or not f.estimate:
                raise BillError(f"{f.id} {f.fingerprint} has no clause and estimate to draft from; decide by hand.")
            continue
        if f.status != EXTRA:
            raise BillError(f"{f.id} {f.fingerprint} is '{f.status}', not a possible extra, so scopeguard will not draft it. "
                            "Edit the SOW or decide by hand if you disagree.")
    out = Path(out_dir)
    co = out / "change-orders"
    co.mkdir(parents=True, exist_ok=True)
    ledger = ledger_for(data, out)
    written = []
    by_fp = {d["fingerprint"]: d for d in data["findings"]}
    for f in chosen:
        name = f"{f.fingerprint}-{slug(f.title)}.md"
        (co / name).write_text(draft(f, card, tone, data.get("revision_limit")), encoding="utf-8")
        ledger.record(f.fingerprint, "billed", quote=f.request.quote[:300], date=f.request.ts, sender=f.request.sender,
                      draft=f"change-orders/{name}", clause=f.clause_id or "")
        by_fp[f.fingerprint]["draft_file"] = f"change-orders/{name}"
        by_fp[f.fingerprint]["status"] = BILLED
        written.append(co / name)
    _save(out, data, ledger)
    return written


def dismiss(out_dir: str | Path, ids: list[str]) -> list[Finding]:
    data, findings = load_findings(out_dir)
    chosen = select(findings, ids)
    ledger = ledger_for(data, out_dir)
    by_fp = {d["fingerprint"]: d for d in data["findings"]}
    for f in chosen:
        ledger.record(f.fingerprint, "dismissed", quote=f.request.quote[:300], date=f.request.ts, sender=f.request.sender,
                      clause=f.clause_id or "")
        by_fp[f.fingerprint]["status"] = DISMISSED
    _save(Path(out_dir), data, ledger)
    return chosen
