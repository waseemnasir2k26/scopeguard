# Changelog

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
