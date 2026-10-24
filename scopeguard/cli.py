from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .llm import LLMError, make_backend
from .models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR
from .outputs import counts, write_all
from .pipeline import Options, analyze
from .rates import SAMPLE, load_rates
from .sow import load_text, parse_sow


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--sow", required=True, help="agreed scope: .md, .txt, .pdf or .docx")
    p.add_argument("--rates", help="rates.yaml (hourly rate, currency, per-task estimates)")
    p.add_argument("--llm", default="auto", help="auto (local Ollama), ollama, groq, gemini or mock (default auto)")
    p.add_argument("--model", help="override the model name for the chosen backend")
    p.add_argument("--allow-cloud", action="store_true", help="let --llm auto fall back to Groq/Gemini (sends text off this machine)")
    p.add_argument("--client", action="append", default=[], help="client's sender name (repeatable)")
    p.add_argument("--me", action="append", default=[], help="your own sender name to ignore (repeatable)")
    p.add_argument("--tone", choices=["friendly", "neutral", "firm"], default="friendly")
    p.add_argument("--redact", action="store_true", help="mask emails and phone numbers before any LLM call")
    p.add_argument("--llm-extract", action="store_true", help="also ask the LLM to find requests (quotes must be verbatim)")
    p.add_argument("--min-confidence", type=float, default=0.5, help="below this, IN/OUT becomes UNCLEAR (default 0.5)")
    p.add_argument("--out", default="out", help="output folder (default ./out)")


def _build(args) -> tuple[Options, object, str]:
    llm = make_backend(args.llm, args.allow_cloud, args.model)
    if getattr(llm, "is_cloud", False):
        print(f"NOTICE: using cloud model {llm.label()}: scope text and requests leave this machine"
              f"{'' if args.redact else ' (consider --redact)'}.", file=sys.stderr)
    card = load_rates(args.rates)
    if card.placeholder:
        print("NOTICE: no --rates file; using a placeholder 50 USD/h. Run `scopeguard init` for a template.", file=sys.stderr)
    sow_text = load_text(args.sow)
    opts = Options(llm=llm, client=args.client, me=args.me, rates=card, tone=args.tone, redact=args.redact,
                   llm_extract=args.llm_extract, min_confidence=args.min_confidence)
    return opts, parse_sow(sow_text), sow_text


def cmd_analyze(args) -> int:
    opts, sow, sow_text = _build(args)
    print(f"model: {opts.llm.label()} | clauses: {len(sow.clauses)} | revisions included: {sow.revision_limit}", file=sys.stderr)

    def prog(i, n, f):
        print(f"  [{i}/{n}] {f.cls:12s} {f.confidence:.2f}  {f.request.quote[:70]}", file=sys.stderr)

    opts.progress = prog
    res = analyze(sow, args.messages, opts, sow_text=sow_text, fmt=args.format)
    out = write_all(res, args.out, opts.rates, args.tone)
    c = counts(res)
    for w in res.warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"{len(res.findings)} requests: {c[IN_SCOPE]} in-scope, {c[OUT_OF_SCOPE]} out-of-scope, {c[UNCLEAR]} unclear "
          f"({res.meta['seconds']} s, {res.meta['llm']})")
    print(f"wrote {out / 'report.html'}, summary.md, scope-log.csv, change-orders/ (drafts, never sent)")
    return 0


def cmd_watch(args) -> int:
    from .watch import watch
    opts, sow, sow_text = _build(args)
    print(f"watching {args.folder} (Ctrl+C to stop)", file=sys.stderr)
    try:
        watch(args.folder, sow, sow_text, opts, args.out, args.interval, args.once)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_serve(args) -> int:
    from .server import BindError, make_server
    token = args.token or os.environ.get("SCOPEGUARD_TOKEN")
    opts, sow, sow_text = _build(args)
    try:
        srv = make_server(args.host, args.port, sow, sow_text, opts, token)
    except BindError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"scopeguard listening on http://{args.host}:{args.port} (POST /analyze, GET /healthz; "
          f"token {'required' if token else 'not set'})", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_eval(args) -> int:
    from .evaluate import format_report, load_labels, run_eval
    llm = make_backend(args.llm, args.allow_cloud, args.model)
    sow = parse_sow(load_text(args.sow))
    items = load_labels(args.labels)

    def prog(i, n, row):
        print(f"  [{i}/{n}] {'ok  ' if row['ok'] else 'MISS'} {row['got']:12s} {row['text'][:60]}", file=sys.stderr)

    res = run_eval(sow, items, llm, args.min_confidence, prog if args.verbose else None)
    print(format_report(res))
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    if not res["revision_cases"]["all_correct"]:
        print("FAIL: not every revision case was classified correctly", file=sys.stderr)
        return 3
    return 0


def cmd_init(args) -> int:
    p = Path(args.path)
    if p.exists() and not args.force:
        print(f"{p} exists; use --force to overwrite", file=sys.stderr)
        return 1
    p.write_text(SAMPLE, encoding="utf-8")
    print(f"wrote {p}")
    return 0


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="scopeguard", description="Scope-creep detector for freelancers. Drafts only; never sends.")
    ap.add_argument("--version", action="version", version=f"scopeguard {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="classify client requests against the SOW")
    _common(a)
    a.add_argument("--messages", required=True, nargs="+", help="message export file(s) or folder(s)")
    a.add_argument("--format", default="auto", choices=["auto", "whatsapp", "eml", "mbox", "json", "upwork", "slack", "chat", "txt"])
    a.set_defaults(fn=cmd_analyze)

    w = sub.add_parser("watch", help="analyse new message exports dropped in a folder")
    _common(w)
    w.add_argument("folder")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--once", action="store_true", help="scan once and exit")
    w.set_defaults(fn=cmd_watch)

    s = sub.add_parser("serve", help="local HTTP API (for n8n)")
    _common(s)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--token", help="required for non-loopback hosts (or set SCOPEGUARD_TOKEN)")
    s.set_defaults(fn=cmd_serve)

    e = sub.add_parser("eval", help="measure accuracy on a labelled request set (see examples/eval/labels.json)")
    e.add_argument("--sow", required=True)
    e.add_argument("--labels", required=True)
    e.add_argument("--llm", default="none", help="none (rules only, default), mock, ollama, groq, gemini")
    e.add_argument("--model")
    e.add_argument("--allow-cloud", action="store_true")
    e.add_argument("--min-confidence", type=float, default=0.5)
    e.add_argument("--json", help="also write the full result here")
    e.add_argument("-v", "--verbose", action="store_true")
    e.set_defaults(fn=cmd_eval)

    i = sub.add_parser("init", help="write a sample rates.yaml")
    i.add_argument("path", nargs="?", default="rates.yaml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (LLMError, ValueError, OSError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
