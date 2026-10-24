"""`scopeguard eval`: measure the classifier against a labelled request set."""
from __future__ import annotations

import json
from pathlib import Path

from .classify import classify_one
from .extract import is_revision
from .models import CLASSES, Request, Sow


def load_labels(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["items"] if isinstance(data, dict) else data
    for it in items:
        if it["expect"] not in CLASSES or not isinstance(it.get("text"), str):
            raise ValueError(f"bad label: {it!r}")
    return items


def run_eval(sow: Sow, items: list[dict], llm, min_confidence: float = 0.5, progress=None) -> dict:
    rows = []
    for i, it in enumerate(items, 1):
        rev = is_revision(it["text"])
        req = Request(id=it.get("id", f"L{i}"), message_id=f"M{i}", sender="Client", ts=None, quote=it["text"],
                      is_revision=rev, revision_round=int(it.get("round", 1)) if rev else 0)
        f = classify_one(req, i, sow, llm, lambda s: s, min_confidence)
        rows.append({"id": req.id, "text": it["text"], "tag": it.get("tag", ""), "expect": it["expect"],
                     "got": f.cls, "rule": f.rule, "clause": f.clause_id, "ok": f.cls == it["expect"],
                     "detected_revision": rev})
        if progress:
            progress(i, len(items), rows[-1])
    per = {}
    for c in CLASSES:
        exp = [r for r in rows if r["expect"] == c]
        got = [r for r in rows if r["got"] == c]
        tp = sum(1 for r in exp if r["ok"])
        per[c] = {"n": len(exp), "correct": tp, "recall": round(tp / len(exp), 3) if exp else None,
                  "precision": round(tp / len(got), 3) if got else None}
    rev_rows = [r for r in rows if r["tag"] == "revision"]
    return {
        "model": llm.label(), "n": len(rows),
        "overall": round(sum(r["ok"] for r in rows) / len(rows), 3) if rows else None,
        "per_class": per,
        "revision_cases": {"n": len(rev_rows), "correct": sum(r["ok"] for r in rev_rows),
                           "all_correct": all(r["ok"] for r in rev_rows)},
        "misses": [r for r in rows if not r["ok"]], "rows": rows,
    }


def format_report(res: dict) -> str:
    L = [f"model: {res['model']}   labelled requests: {res['n']}   overall accuracy: {res['overall']:.1%}", ""]
    L.append(f"{'class':14s} {'n':>3s} {'correct':>8s} {'recall':>7s} {'precision':>10s}")
    for c, v in res["per_class"].items():
        rec = "n/a" if v["recall"] is None else f"{v['recall']:.0%}"
        pre = "n/a" if v["precision"] is None else f"{v['precision']:.0%}"
        L.append(f"{c:14s} {v['n']:3d} {v['correct']:8d} {rec:>7s} {pre:>10s}")
    r = res["revision_cases"]
    L.append(f"\nrevision cases: {r['correct']}/{r['n']} correct")
    if res["misses"]:
        L.append("\nmisses:")
        for m in res["misses"]:
            L.append(f"  [{m['tag']}] expected {m['expect']}, got {m['got']}: {m['text']}")
    return "\n".join(L)
