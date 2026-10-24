# scopeguard: scope-creep detector for freelancers and small agencies

![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Python 3.10+](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)
![Local first](https://img.shields.io/badge/LLM-local%20by%20default-informational.svg)

**You agreed to five pages. Three weeks later it is a shop, a blog and a Spanish version, and saying "that's extra" feels rude. This does the awkward part for you.**

Give scopeguard the agreed scope (SOW or proposal) and the client's messages (WhatsApp, email, Slack, Upwork). It finds every request, marks each one **IN-SCOPE**, **OUT-OF-SCOPE** or **UNCLEAR**, quotes the client's words, points at the SOW clause that decides it, and writes a polite change-order **draft** with an estimate from your own rate card. It never sends anything.

## 2-minute quickstart

```bash
git clone https://github.com/waseemnasir2k26/scopeguard.git
cd scopeguard
pip install .            # Python 3.10+, one dependency (PyYAML). PDFs: pip install ".[pdf]"

# offline demo, no model needed (lexical matching only; see "How it works")
scopeguard analyze --sow examples/sow.md --messages examples/whatsapp-chat.txt \
  --client Dana --rates examples/rates.yaml --llm mock --out out

# with a local model (Ollama running; any model you have pulled)
SCOPEGUARD_OLLAMA_MODEL=qwen3:8b scopeguard analyze --sow examples/sow.md \
  --messages examples/whatsapp-chat.txt --client Dana --rates examples/rates.yaml --out out
```

Open `out/report.html`. Create your own rate card with `scopeguard init`.

You get, in `out/`:

| File                 | What                                                               |
| -------------------- | ------------------------------------------------------------------ |
| `report.html`        | One self-contained page (no external requests), filter by class    |
| `change-orders/*.md` | One draft per out-of-scope request. Review, edit, send it yourself |
| `scope-log.csv`      | Every finding; spreadsheet-formula safe                            |
| `summary.md`         | Counts, estimated unbilled work, revision rounds used              |
| `findings.json`      | Same data for scripts                                              |

## Real output

Run on the bundled synthetic example (`examples/sow.md`, a 5-page website; `examples/whatsapp-chat.txt`, 21 client requests from a made-up client). Model: **`skynetlabs-assistant` via local Ollama (a 2.5 GB model), 157.6 s on one consumer GPU** (an earlier identical run took 52 s; the box was shared). Result: **6 in-scope, 13 out-of-scope, 2 unclear.** Full files are in [`examples/sample-output/`](examples/sample-output/).

```
REQ-03 OUT-OF-SCOPE  C12 explicit_exclusion  "One more thing, can we add an online shop so people can buy our prints?"
REQ-09 OUT-OF-SCOPE  C13 explicit_exclusion  "Could we also have a blog?"                       6 h x $60 = $360 (rate card)
REQ-16 OUT-OF-SCOPE  C10 revision_limit      "Can you change the heading font to something rounder?"   (revision round 3 of 2)
REQ-14 UNCLEAR       no clause               "Could you make the site feel more premium?"
REQ-02 IN-SCOPE      C4                      "And please make sure the whole site works well on phones."
```

Be honest about what this sample shows: the small model got several calls wrong (it called "make the logo bigger" out of scope, and the invoice-address message is not a scope question at all), it is over-confident (many 1.00 scores), and the headline "estimated unbilled work" of $6,390 is inflated by split and duplicate requests ("Could we also have a blog?" and "write the first three posts" are one ask). Use a stronger model for real work, and treat every row as a lead to check, not a verdict. The rule layer caught the exclusions and the third revision round regardless of the model.

## How classification works

1. **Parse the SOW** (`.md`, `.txt`, `.pdf`, `.docx`) into numbered clauses (`C1`, `C2`, with the SOW's own numbering such as `1.1` kept). Each clause is tagged deliverable, exclusion, revision, timeline or other. "Two rounds of revisions" becomes a revision limit of 2.
2. **Parse the messages** with tolerant parsers: WhatsApp (Android `12/03/2026, 14:05 - Name: text` and iOS `[12/03/26, 2:05:33 PM] Name: text`, day-first or month-first), `.eml`, `.mbox` (quoted replies stripped), Slack/Upwork `.json`, Slack/Upwork pasted `.txt`, plain pasted text.
3. **Extract requests.** Heuristics split client messages into sentences and keep the ones that ask for something. `--llm-extract` adds an LLM pass. **Evidence rule:** a request is kept only if its quote appears verbatim in the messages (case and whitespace aside); anything else is dropped and counted.
4. **Classify** each request with an LLM and a strict JSON schema (class, clause id, confidence, reason, estimated hours). Clause citations are checked against the real clauses: an invented clause is dropped and an IN-SCOPE verdict without a real clause becomes UNCLEAR. Below `--min-confidence` (default 0.5), IN/OUT becomes UNCLEAR.
5. **Rule layer** (deterministic, wins over the model):
   - _Explicit exclusion:_ the request overlaps an exclusion clause more than any deliverable does (word and concept overlap, e.g. "online shop" vs "e-commerce"). Revision requests are skipped here.
   - _Revision limit:_ revision-type requests are grouped into rounds (same message, or messages under 30 minutes apart). Round 3 of 2 included is OUT-OF-SCOPE. Round counting is a heuristic; check it.
6. **Estimate.** Hours come from your `rates.yaml` task match first, then the LLM's proposed hours, then your default; rounded up to a quarter hour, never below your minimum. Always labelled an estimate.

Backends: local Ollama (`SCOPEGUARD_OLLAMA_MODEL`, else the first model Ollama lists), then opt-in cloud (`--llm groq` with `GROQ_API_KEY`, `--llm gemini` with `GEMINI_API_KEY`; `--llm auto --allow-cloud` falls back to them), or `--llm mock` for offline use. The cloud backends are implemented against the documented APIs but are not covered by the automated tests (they need keys).

### rates.yaml

```yaml
hourly_rate: 60
currency: USD
minimum_hours: 0.5
default_hours: 2
tasks: # optional; first keyword match wins
  - { match: [blog], hours: 6, label: Blog setup }
  - { match: [translation, spanish], fixed: 450, label: Translation }
```

### Tone

`--tone friendly|neutral|firm` changes the wording of the drafts.

## Watch a folder, or call it from n8n

```bash
scopeguard watch ./inbox --sow sow.md --rates rates.yaml --client "Dana Okafor" --out out
scopeguard serve --sow sow.md --rates rates.yaml --client "Dana Okafor"   # http://127.0.0.1:8765
```

`serve` binds to `127.0.0.1`. Any other address is refused unless you pass `--token` (or `SCOPEGUARD_TOKEN`). [`examples/n8n-workflow.json`](examples/n8n-workflow.json) is a Gmail-label trigger that posts to the server and creates a Slack alert and a Gmail **draft** in your own mailbox. Setup in [docs/n8n.md](docs/n8n.md).

## Privacy

Everything runs locally by default: files are read from disk, the local model runs on your machine, outputs are written to `./out`. A cloud LLM is opt-in (`--llm groq|gemini`, or `--allow-cloud`) and prints a notice when used. `--redact` masks emails and phone numbers before any LLM call (the report keeps the originals). Redaction is pattern-based and will miss names, addresses and anything unusual. Do not commit real client exports; `.gitignore` covers `out/` and common export names. The repo contains synthetic data only.

## Limits

- **LLM judgement is a draft. You decide.** Models misread context, and small local ones are over-confident. Read every row before you say anything to a client.
- Not legal advice. A change-order draft is a polite message, not a contract term. Check your actual agreement.
- The rule layer is lexical. It can miss a paraphrased exclusion and can over-match a shared word; revision-round counting uses a 30-minute heuristic and cannot know which deliverable a round belongs to.
- The heuristic extractor can split one ask into two and can miss requests with no cue words. It also treats whoever is not `--client` / `--me` as the client only if it can tell; with no flag it guesses from names in the SOW, or analyses everyone and warns.
- Chat context across messages ("yes, do that") is not resolved. Attachments and images are ignored.
- PDF text extraction depends on the PDF having a text layer. Scanned SOWs need OCR first.
- Estimates multiply your rate by hours that a model guessed. Edit them.
- The Groq and Gemini backends are untested against the live APIs.

## For you if

- You freelance or run a small agency on fixed-price or capped-scope work and lose hours to "one more thing".
- Your client talks to you in WhatsApp, email threads or Upwork messages, and your SOW is a document, not a ticket system.
- You want a second pair of eyes and a draft reply, not an auto-sender.

Not for you if you need a legally reviewed contract engine or fully automatic client replies.

## Tests

```bash
pip install ".[dev]"
python -m pytest
```

59 tests: parsers (WhatsApp Android and iOS, eml, mbox, txt, Slack/Upwork), clause splitter, revision counter, evidence rule, mock-LLM classification, change-order math, HTML and CSV escaping, never-send, redaction, server bind guard and auth.

## Need this wired into your business?

SkynetLabs builds voice agents, n8n back offices and sites that convert → https://skynetjoe.com

## License

MIT, 2026 SkynetLabs. See [LICENSE](LICENSE).
