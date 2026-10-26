"""Single self-contained report.html: everything escaped, light theme, grouped as
Possible extras / Needs your call / In scope / Set aside, filterable."""
from __future__ import annotations

from html import escape

from .changeorder import headline, money
from .rates import RateCard
from .status import BILLED, CALL, DISMISSED, EXTRA, FREE, IN, LABELS, SET_ASIDE, WITHDRAWN, billed_then_withdrawn

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1b2430;--mute:#5b6675;--line:#e1e5eb;--in:#1c7c4d;--out:#b3261e;--unc:#9a6700;--aside:#6b7280}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 4px}
.sub{color:var(--mute);margin:0 0 16px}.warn{background:#fff7e0;border:1px solid #f0d890;padding:8px 12px;border-radius:8px;margin:8px 0;font-size:14px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.bar button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 12px;border-radius:999px;cursor:pointer;font:inherit}
.bar button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
.card{background:var(--card);border:1px solid var(--line);border-left-width:5px;border-radius:10px;padding:14px 16px;margin:12px 0}
.card.extra{border-left-color:var(--out)}.card.in{border-left-color:var(--in)}.card.call{border-left-color:var(--unc)}.card.aside{border-left-color:var(--aside);opacity:.85}
.tag{font-weight:600;font-size:12px;letter-spacing:.04em}.extra .tag{color:var(--out)}.in .tag{color:var(--in)}.call .tag{color:var(--unc)}.aside .tag{color:var(--aside)}
blockquote{margin:8px 0;padding:6px 12px;background:#f1f4f8;border-radius:6px;white-space:pre-wrap}
.meta{color:var(--mute);font-size:13px}dl{margin:8px 0 0;display:grid;grid-template-columns:110px 1fr;gap:4px 12px}dd{margin:0}dt{color:var(--mute)}
code{background:#eef1f5;padding:1px 5px;border-radius:4px}details{margin-top:8px}.foot{color:var(--mute);font-size:13px;margin-top:32px}
@media(max-width:560px){dl{grid-template-columns:1fr}}
"""

JS = """
(function(){var b=document.querySelectorAll('.bar button'),c=document.querySelectorAll('.card'),h=document.querySelectorAll('h2');
b.forEach(function(x){x.addEventListener('click',function(){b.forEach(function(y){y.setAttribute('aria-pressed','false')});
x.setAttribute('aria-pressed','true');var f=x.getAttribute('data-f');
c.forEach(function(k){k.hidden=!(f==='ALL'||k.getAttribute('data-g')===f)});
h.forEach(function(k){k.hidden=!(f==='ALL'||k.getAttribute('data-g')===f)})})})})();
"""

GROUPS = (("extra", "Possible extras", [EXTRA], "Things that look outside the agreed scope. Talk to the client first; you decide."),
          ("call", "Needs your call", [CALL], "Unclear, conditional, hypothetical or not classifiable. Nothing here is priced."),
          ("in", "In scope", [IN], "Covered by the agreement."),
          ("aside", "Set aside", list(SET_ASIDE), "Withdrawn by the client, waived by you, dismissed, or already drafted."))


def _e(v) -> str:
    return escape("" if v is None else str(v), quote=True)


def _card(f, group: str) -> list[str]:
    e = f.estimate
    when = f.request.ts.replace("T", " ") if f.request.ts else "unknown time"
    p = [f"<section class='card {group}' data-g='{group}'>",
         f"<div class=tag>{_e(LABELS.get(f.status, f.status)).upper()} &middot; {_e(f.id)} <code>{_e(f.fingerprint)}</code> "
         f"&middot; {_e(f.cls)} &middot; confidence {f.confidence:.2f}{' &middot; rule: ' + _e(f.rule) if f.rule else ''}</div>"]
    for q in f.request.all_quotes():
        p.append(f"<blockquote>{_e(q)}</blockquote>")
    p.append(f"<div class=meta>{_e(f.request.sender)} &middot; {_e(when)}</div><dl>")
    if f.clause_id:
        ref = f" (SOW {_e(f.clause_ref)})" if f.clause_ref else ""
        p.append(f"<dt>Clause</dt><dd><strong>{_e(f.clause_id)}</strong>{ref}: {_e(f.clause_text)}</dd>")
    else:
        p.append("<dt>Clause</dt><dd>no matching clause</dd>")
    p.append(f"<dt>Why</dt><dd>{_e(f.reason)}</dd>")
    if f.request.withdrawn_by:
        p.append(f"<dt>Withdrawn by</dt><dd>&ldquo;{_e(f.request.withdrawn_by)}&rdquo;</dd>")
    if f.free_by:
        p.append(f"<dt>You said</dt><dd>&ldquo;{_e(f.free_by)}&rdquo; (treated as included, never billed)</dd>")
    if f.status == EXTRA and f.latest_related:
        p.append(f"<dt>Latest related</dt><dd>{_e(f.latest_related)} <span class=meta>(the last thing the client said about this)</span></dd>")
    if e and f.status == EXTRA:
        calc = money(e["amount"], e["currency"])
        if e.get("hours") is not None:
            calc = (f"{e['hours']:g} h x {money(e['rate'], e['currency'])}/h = {calc}, range "
                    f"{money(e['amount_low'], e['currency'])} to {money(e['amount_high'], e['currency'])} "
                    f"({e['hours_low']:g}-{e['hours_high']:g} h)")
        p.append(f"<dt>Estimate</dt><dd>{_e(calc)} <span class=meta>(estimate; basis: {_e(e['basis'])})</span></dd>")
        p.append(f"<dt>If you agree</dt><dd><code>scopeguard bill {_e(f.fingerprint)}</code> drafts a change order; "
                 f"<code>scopeguard dismiss {_e(f.fingerprint)}</code> hides this item.</dd>")
    if f.draft_file:
        p.append(f"<dt>Draft</dt><dd><code>{_e(f.draft_file)}</code> (never sent)</dd>")
    p.append("</dl>")
    if f.notes:
        p.append("<details><summary>Notes</summary><ul>" + "".join(f"<li>{_e(n)}</li>" for n in f.notes) + "</ul></details>")
    p.append("</section>")
    return p


def render_html(res, card: RateCard) -> str:
    m = res.meta
    by = {g: [f for f in res.findings if f.status in st] for g, _, st, _ in GROUPS}
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<meta http-equiv=Content-Security-Policy content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">",
        "<title>scopeguard report</title><style>" + CSS + "</style></head><body><main>",
        "<h1>scopeguard report</h1>",
        f"<p class=sub>{_e(m['llm'])} ({'cloud' if m['cloud'] else 'local'}) &middot; {m['requests']} items &middot; "
        f"{m['seconds']} s &middot; redaction {'on' if m['redacted'] else 'off'}</p>",
        f"<p><strong>{_e(headline(res.findings, card.currency))}</strong> "
        "<span class=meta>(a rough band from your rate card, not an invoice)</span></p>",
        "<p class=meta>scopeguard suggests; you decide. Nothing has been drafted or sent. A model helped make these calls; "
        "read each one before you say anything to the client.</p>",
    ]
    if m.get("revision_limit"):
        parts.append(f"<p class=meta>Revision rounds used: {m['revision_rounds_used']} of {m['revision_limit']} included.</p>")
    if m.get("other_client_messages_total"):
        parts.append(f"<p class=meta>{m['other_client_messages_total']} other client sentences were not turned into items; "
                     "they are listed at the bottom with the reason.</p>")
    for f in billed_then_withdrawn(res.findings):
        parts.append(f"<div class=warn><strong>CRITICAL</strong> {_e(f.id)} <code>{_e(f.fingerprint)}</code>: {_e(f.reason)}</div>")
    for w in res.warnings:
        parts.append(f"<div class=warn>{_e(w)}</div>")
    parts.append("<div class=bar role=group aria-label=Filter>")
    parts.append(f"<button type=button data-f=ALL aria-pressed=true>All ({len(res.findings)})</button>")
    for g, title, _, _ in GROUPS:
        parts.append(f"<button type=button data-f={g} aria-pressed=false>{_e(title)} ({len(by[g])})</button>")
    parts.append("</div>")
    for g, title, _, blurb in GROUPS:
        if not by[g]:
            continue
        parts.append(f"<h2 data-g='{g}'>{_e(title)} ({len(by[g])}) <span class=meta>{_e(blurb)}</span></h2>")
        for f in by[g]:
            parts += _card(f, g)
    if not res.findings:
        parts.append("<p>No requests were found in the messages.</p>")
    oth = m.get("other_client_messages") or []
    if m.get("other_client_messages_total"):
        parts.append(f"<details data-g='aside'><summary>Other client messages ({m['other_client_messages_total']}): everything the client "
                     "wrote that was not turned into an item, with the reason</summary><ul>")
        for o in oth[:150]:
            parts.append(f"<li>{_e(o['quote'])} <span class=meta>({_e(o['why'])})</span></li>")
        if m["other_client_messages_total"] > 150:
            parts.append(f"<li class=meta>... and {m['other_client_messages_total'] - 150} more (the first 300 are in findings.json)</li>")
        parts.append("</ul></details>")
    parts.append("<p class=foot>Generated locally by scopeguard 1.1.1. Not legal advice.</p>")
    parts.append("<script>" + JS + "</script></main></body></html>")
    return "".join(parts)
