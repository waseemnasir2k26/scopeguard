# Changelog

## 1.1.3 - 2026-10-26

- `bill` validates the ledger before it creates anything: a corrupt ledger refuses (exit 1) with no draft and no `change-orders/` folder. Order is now load and validate, record the decision (atomic), then write the draft; if the draft or the findings save fails, the ledger entry is rolled back and the partial draft removed.
- Windows: atomic writes use a unique temp name per call and retry `os.replace` on PermissionError (up to 10 attempts with a short backoff), fixing the concurrent-writer flake.
- WhatsApp: a one-line export (and a file without a trailing newline) is detected and parsed; the sender is no longer `client` and the timestamp no longer lands in the quote.
- 10 new regression tests (tests/test_round5.py).

## 1.1.2 - 2026-10-26

- Double-bill fix: a new possible extra that reads as a repeat of an item you already billed or dismissed is held in "Needs your call" with the reason "looks like a repeat of <billed|dismissed> item <id> (<date>): <quote>". The match is stemmed content-word Jaccard >= 0.5, or the same cited clause with two shared content words, against every ledger entry. Generic request words (add, page, site ...) do not count as shared subject. The bill listing shows held repeats; `bill --all-strong` never includes them; `bill <id>` on one refuses unless you pass `--repeat-ok`. The ledger now records the clause of each decision. Findings carry `repeat_of`.
- `--client` for a single-sender export: a non-matching name is ignored with a warning instead of an error. Multi-sender exports still refuse a name that matches nobody.
- 12 new regression tests (tests/test_round4.py); 239 pass.

## 1.1.1 - 2026-10-25

- Withdrawals no longer depend on a closed vocabulary: a safety net moves any possible extra to "Needs your call" when a later client message about the same thing is not a plain request for it ("later message mentions this: ..."). Retraction wording widened (cancel, drop, skip, scratch, ignore, remove, hold off, pause, disregard, leave out, won't do, no longer required, no X please, pls/plz). `bill --all-strong` cannot include such items and its listing shows the latest related client message.
- Every client sentence is accounted for: an item, a retraction linked to its item, or "Other client messages" in the report with the reason.
- Stable ids: `sha1(client | wording | n-th identical ask)`, not the timestamp; the ledger re-binds by quote similarity (>= 0.95) with a warning.
- Ledger: an unreadable ledger stops the run (never treated as empty); fsynced atomic writes keep a `.bak` on every write; `scopeguard ledger repair`. `findings.json` is written atomically under the same lock; `bill` refuses a ledger outside the output folder.
- `watch` and `serve` accumulate each client's thread (`<data>/<client>/messages.jsonl`) and re-analyse it whole, with the ledger applied.
- `--client` matching several senders, or the freelancer named in the SOW, is refused with the sender list.
- Consolidation needs the same clause and 80% overlap; "store" alone is no longer e-commerce; more revision words (rounder, sharper, softer, crisper); long quotes are cut in drafts; `--redact` help matches what redaction does.

## 1.1.0 - 2026-10-25

Structural change: scopeguard suggests, you decide.

- `analyze` never writes change-order drafts. Items are sorted into Possible extras / Needs your call / In scope / Set aside. Headline: "Possible extra work to discuss: N items ($A to $B if all are confirmed)".
- New `scopeguard bill <id>...` (or `--all-strong` with a y/N listing) drafts change orders for the items you choose; `scopeguard dismiss <id>` records "not an extra". Decisions persist in a lock-protected, atomically written `ledger.json`; item ids are stable fingerprints.
- Stateful withdrawal: a retraction in the same message or any later message that references an item withdraws it. Conditionals, hedges, sarcasm and conflicting statements are "Needs your call". Freelancer free offers ("on the house", "no extra charge") mark items included by you.
- `--client` is required for multi-sender exports and matched on whole name tokens; no guessing from the SOW.
- Caps: the most relevant cap only, "minimum" and "per row" are not caps, supplied totals ("40 photos, use the best 20") are not requests, number words / hyphenated / dozens, "a couple/a few/N more" are not priced, literal email addresses are counted.
- SOW exceptions inside exclusions ("except one launch post, which is included"); client-authored assets ("the logo I designed").
- LLM trust: an OUT verdict must cite an exclusion the request mentions; citing a deliverable contradicts the SOW; IN verdicts need support too.
- "Skipped" sentences are counted and listed, never dropped silently. Simple deliverables ("page titles", "social links in the footer") are matched in rules-only mode.
- Eval: 78 labelled items plus a 20-item held-out set; the two critics' probe files are in the tests (172 tests).
- n8n example sends an internal Slack alert only (no Gmail draft node).

## 1.0.0 - 2026-10-25

First release.

- SOW parsing (.md, .txt, .pdf, .docx) into numbered clauses: deliverables, exclusions, revision limits, timeline.
- Message parsers: WhatsApp (Android and iOS), .eml, .mbox, Slack/Upwork .json and .txt, plain pasted text.
- Request extraction (heuristics, optional LLM) with a verbatim-quote evidence rule.
- Classification: IN-SCOPE / OUT-OF-SCOPE / UNCLEAR via local Ollama, opt-in Groq/Gemini, or an offline mock; strict JSON, citation checks, deterministic rules for exclusions and revision limits.
- `rates.yaml` rate card and change-order drafts (friendly/neutral/firm). Drafts are files; nothing is sent.
- Outputs: `report.html`, `change-orders/*.md`, `scope-log.csv`, `summary.md`, `findings.json`.
- `scopeguard watch`, `scopeguard serve` (loopback by default, token for anything else), n8n example workflow.
- Deterministic layer decides first: revision requests against the SOW revision allowance, explicit exclusions by phrase. The model decides the rest and must cite a clause; clause-less OUT-OF-SCOPE becomes UNCLEAR.
- Request consolidation (split and duplicate asks priced once); extra-work headline as a range.
- `scopeguard eval` with a 42-item labelled set; `--llm none` rules-only mode.
- Billing safety: retractions and hypotheticals are never requests; client-supplied assets are included; numeric caps from the SOW; a page the SOW names is never an extra page; consolidation only merges same-verdict items; drafts need a cited clause and confidence >= 0.75, open with a soft check, and hedge revision counts.
- Evidence rule uses an inverted index (50,000 messages in about 2 s); input capped at 200,000 messages.
- Server: Host header check, read timeout, token on `/healthz`. Drafts escape Markdown and strip newlines from subjects.
- `--redact` masks emails and phone numbers before any LLM call.
