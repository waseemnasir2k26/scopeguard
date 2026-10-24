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
- `--redact` masks emails and phone numbers before any LLM call.
