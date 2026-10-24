"""Single self-contained report.html: everything escaped, light theme, filter by class."""
from __future__ import annotations

from html import escape

from .changeorder import headline, money
from .models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR
from .rates import RateCard

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1b2430;--mute:#5b6675;--line:#e1e5eb;--in:#1c7c4d;--out:#b3261e;--unc:#9a6700}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mute);margin:0 0 16px}.warn{background:#fff7e0;border:1px solid #f0d890;padding:8px 12px;border-radius:8px;margin:8px 0;font-size:14px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.bar button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 12px;border-radius:999px;cursor:pointer;font:inherit}
.bar button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
.card{background:var(--card);border:1px solid var(--line);border-left-width:5px;border-radius:10px;padding:14px 16px;margin:12px 0}
.card.IN-SCOPE{border-left-color:var(--in)}.card.OUT-OF-SCOPE{border-left-color:var(--out)}.card.UNCLEAR{border-left-color:var(--unc)}
.tag{font-weight:600;font-size:12px;letter-spacing:.04em}.IN-SCOPE .tag{color:var(--in)}.OUT-OF-SCOPE .tag{color:var(--out)}.UNCLEAR .tag{color:var(--unc)}
blockquote{margin:8px 0;padding:6px 12px;background:#f1f4f8;border-radius:6px;white-space:pre-wrap}
.meta{color:var(--mute);font-size:13px}.k{color:var(--mute)}dl{margin:8px 0 0;display:grid;grid-template-columns:110px 1fr;gap:4px 12px}dd{margin:0}dt{color:var(--mute)}
details{margin-top:8px}.foot{color:var(--mute);font-size:13px;margin-top:32px}
@media(max-width:560px){dl{grid-template-columns:1fr}}
"""

JS = """
(function(){var b=document.querySelectorAll('.bar button'),c=document.querySelectorAll('.card');
b.forEach(function(x){x.addEventListener('click',function(){b.forEach(function(y){y.setAttribute('aria-pressed','false')});
x.setAttribute('aria-pressed','true');var f=x.getAttribute('data-f');
c.forEach(function(k){k.hidden=!(f==='ALL'||k.getAttribute('data-c')===f)})})})})();
"""


def _e(v) -> str:
    return escape("" if v is None else str(v), quote=True)


def render_html(res, card: RateCard) -> str:
    c = {IN_SCOPE: 0, OUT_OF_SCOPE: 0, UNCLEAR: 0}
    for f in res.findings:
        c[f.cls] += 1
    m = res.meta
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<meta http-equiv=Content-Security-Policy content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">",
        "<title>scopeguard report</title><style>" + CSS + "</style></head><body><main>",
        "<h1>scopeguard report</h1>",
        f"<p class=sub>{_e(m['llm'])} ({'cloud' if m['cloud'] else 'local'}) &middot; {m['requests']} requests &middot; "
        f"{m['seconds']} s &middot; redaction {'on' if m['redacted'] else 'off'}</p>",
        f"<p><strong>{_e(headline(res.findings, card.currency))}</strong> "
        "<span class=meta>(a rough band from your rate card, not an invoice)</span></p>",
        "<p class=meta>Draft analysis only. A model made these calls; you decide what to raise. Nothing was sent to anyone.</p>",
    ]
    if m.get("revision_limit"):
        parts.append(f"<p class=meta>Revision rounds used: {m['revision_rounds_used']} of {m['revision_limit']} included.</p>")
    for w in res.warnings:
        parts.append(f"<div class=warn>{_e(w)}</div>")
    parts.append("<div class=bar role=group aria-label=Filter>")
    parts.append(f"<button type=button data-f=ALL aria-pressed=true>All ({len(res.findings)})</button>")
    for cls, label in ((OUT_OF_SCOPE, "Out of scope"), (UNCLEAR, "Unclear"), (IN_SCOPE, "In scope")):
        parts.append(f"<button type=button data-f={cls} aria-pressed=false>{label} ({c[cls]})</button>")
    parts.append("</div>")
    for f in res.findings:
        e = f.estimate
        when = f.request.ts.replace("T", " ") if f.request.ts else "unknown time"
        parts.append(f"<section class='card {f.cls}' data-c='{f.cls}'>")
        parts.append(f"<div class=tag>{_e(f.cls)} &middot; {_e(f.id)} &middot; confidence {f.confidence:.2f}"
                     f"{' &middot; rule: ' + _e(f.rule) if f.rule else ''}</div>")
        for q in f.request.all_quotes():
            parts.append(f"<blockquote>{_e(q)}</blockquote>")
        parts.append(f"<div class=meta>{_e(f.request.sender)} &middot; {_e(when)}</div>")
        parts.append("<dl>")
        if f.clause_id:
            ref = f" (SOW {_e(f.clause_ref)})" if f.clause_ref else ""
            parts.append(f"<dt>Clause</dt><dd><strong>{_e(f.clause_id)}</strong>{ref}: {_e(f.clause_text)}</dd>")
        else:
            parts.append("<dt>Clause</dt><dd>no matching clause</dd>")
        parts.append(f"<dt>Why</dt><dd>{_e(f.reason)}</dd>")
        if e:
            calc = money(e["amount"], e["currency"])
            if e.get("hours") is not None:
                calc = (f"{e['hours']:g} h x {money(e['rate'], e['currency'])}/h = {calc}, range "
                        f"{money(e['amount_low'], e['currency'])} to {money(e['amount_high'], e['currency'])} "
                        f"({e['hours_low']:g}-{e['hours_high']:g} h)")
            parts.append(f"<dt>Estimate</dt><dd>{_e(calc)} <span class=meta>(estimate; basis: {_e(e['basis'])})</span></dd>")
        if f.draft_file:
            parts.append(f"<dt>Draft</dt><dd><code>{_e(f.draft_file)}</code> (never sent)</dd>")
        parts.append("</dl>")
        if f.notes:
            parts.append("<details><summary>Notes</summary><ul>" + "".join(f"<li>{_e(n)}</li>" for n in f.notes) + "</ul></details>")
        parts.append("</section>")
    if not res.findings:
        parts.append("<p>No requests were found in the messages.</p>")
    parts.append("<p class=foot>Generated locally by scopeguard 1.0.0. Not legal advice.</p>")
    parts.append("<script>" + JS + "</script></main></body></html>")
    return "".join(parts)
