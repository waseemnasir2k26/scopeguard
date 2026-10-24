# scopeguard: scope-creep detector for freelancers and small agencies

![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Python 3.10+](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)
![Local first](https://img.shields.io/badge/LLM-local%20by%20default-informational.svg)

![scopeguard report.html on the bundled example](docs/report-screenshot.png)

_The top of `report.html` for the bundled synthetic example, produced by a 2.5 GB local model. The sample still contains mistakes further down; see "Real output" and "Limits"._

**You agreed to five pages. Three weeks later it is a shop, a blog and a Spanish version, and saying "that's extra" feels rude. This does the awkward part for you.**

Give scopeguard the agreed scope (SOW or proposal) and the client's messages (WhatsApp, email, Slack, Upwork). It finds every request, marks each one **IN-SCOPE**, **OUT-OF-SCOPE** or **UNCLEAR**, quotes the client's words, points at the SOW clause that decides it, and writes a polite change-order **draft** with an estimate range from your own rate card. It never sends anything.

## 2-minute quickstart

```bash
git clone https://github.com/waseemnasir2k26/scopeguard.git
cd scopeguard
pip install .            # Python 3.10+, one dependency (PyYAML). PDFs: pip install ".[pdf]"

# offline demo, no model needed (rules + lexical matching; see "How it works")
scopeguard analyze --sow examples/sow.md --messages examples/whatsapp-chat.txt \
  --client Dana --rates examples/rates.yaml --llm mock --out out

# with a local model (Ollama running; any model you have pulled)
SCOPEGUARD_OLLAMA_MODEL=qwen3:8b scopeguard analyze --sow examples/sow.md \
  --messages examples/whatsapp-chat.txt --client Dana --rates examples/rates.yaml --out out
```

Open `out/report.html`. Create your own rate card with `scopeguard init`.

| File in `out/`       | What                                                                                |
| -------------------- | ----------------------------------------------------------------------------------- |
| `report.html`        | One self-contained page (no external requests), filter by class                     |
| `change-orders/*.md` | One draft per out-of-scope item that cites a clause. Review, edit, send it yourself |
| `scope-log.csv`      | Every finding; spreadsheet-formula safe                                             |
| `summary.md`         | Counts, the extra-work range, revision rounds used                                  |
| `findings.json`      | Same data for scripts                                                               |

## Real output

Bundled synthetic example: `examples/sow.md` (a 5-page website, two revision rounds included) and `examples/whatsapp-chat.txt` (a made-up client). Model: **`skynetlabs-assistant` via local Ollama, a 2.5 GB model; 25.6 s on one consumer GPU.** 19 requests after merging a split ask: **8 in-scope, 10 out-of-scope, 1 unclear.** Headline from the generated `summary.md`:

> **Estimated extra work (review before sending): $3,240.00 to $6,030.00 across 10 items (46.5-93 h, plus 1 fixed-price).**

Files: [`examples/sample-output/`](examples/sample-output/). The rules, not the model, decided the revision rounds (rounds 1 and 2 are IN-SCOPE: "make the logo bigger", "swap the order of the services", "darker blue for the buttons"; round 3, "change the heading font" and "make the footer colour match the logo", is OUT-OF-SCOPE with the revision clause cited and the rate-card default of 2 h each), the page counter ("Meet the Team section on the About page" is IN-SCOPE because About is one of the five listed pages), and the explicit exclusions (shop, text for the Services page, blog, Spanish, booking, newsletter).

What this small model still gets wrong: it called "Can the gallery show 30 photos?" out of scope, which is probably right (the SOW says up to 24), but put 15 hours on it, and it guessed 5 hours for sending form enquiries to two addresses. Several hour guesses are inflated, which is why the range above is wide. A stronger model, or filling the `tasks:` list in your rate card with your own numbers, tightens it. Treat the figure as a prompt to review, not as an invoice.

## Measured accuracy

`scopeguard eval` scores the classifier against 46 labelled requests for the bundled SOW ([`examples/eval/labels.json`](examples/eval/labels.json)): 14 revision requests (rounds 1 to 4), 12 explicit exclusions, 4 page-counter cases (a section on a listed page is IN, a new page past the count is OUT), 3 "beyond a deliverable" or silent items, 8 deliverables, 5 vague ones. The labels were written by the project author, not blind, and the set is small. Read the numbers as a regression check, not a benchmark.

| Path | Overall | IN-SCOPE recall / precision | OUT-OF-SCOPE recall / precision | UNCLEAR recall / precision | Revision cases |
| --- | --- | --- | --- | --- | --- |
| Rules only (`--llm none`) | 76.1% | 60% / 100% | 86% / 100% | 100% / 31% | 14/14 |
| Rules + offline mock (`--llm mock`) | 93.5% | 100% / 87% | 86% / 100% | 100% / 100% | 14/14 |
| Rules + `skynetlabs-assistant` (local) | 93.5% | 95% / 100% | 95% / 91% | 80% / 80% | 14/14 |

Rules-only means: whatever the deterministic layer does not decide becomes UNCLEAR, so it never invents an IN-SCOPE, and its precision on IN and OUT is 100% on this set. `scopeguard eval` exits non-zero if any revision case is wrong.

Misses with the local model: it called "Could the portfolio gallery show 20 photos?" out of scope (the SOW says up to 24), called a vague "look into why the site feels slow" out of scope, and answered UNCLEAR for "a members area where clients log in" (the SOW is silent, and an OUT verdict needs a clause to point at). Rules plus mock misses "renew our domain and sort out the hosting" (a deliverable mentions hosting, so the rule layer steps back) and "30 photos". The mock is a lexical baseline that was developed alongside this label set, so its score flatters it.

Reproduce:

```bash
scopeguard eval --sow examples/sow.md --labels examples/eval/labels.json --llm none
scopeguard eval --sow examples/sow.md --labels examples/eval/labels.json --llm mock
SCOPEGUARD_OLLAMA_MODEL=skynetlabs-assistant scopeguard eval --sow examples/sow.md --labels examples/eval/labels.json --llm ollama
```

## How it works

Deterministic rules first, the model second.

1. **Parse the SOW** (`.md`, `.txt`, `.pdf`, `.docx`) into numbered clauses (`C1`, `C2`, with the SOW's own numbering such as `1.1` kept). Each clause is tagged deliverable, exclusion, revision, timeline or other. "Two rounds of revisions" becomes a revision limit of 2.
2. **Parse the messages** with tolerant parsers: WhatsApp (Android `12/03/2026, 14:05 - Name: text` and iOS `[12/03/26, 2:05:33 PM] Name: text`, day-first or month-first), `.eml`, `.mbox` (quoted replies stripped), Slack/Upwork `.json`, Slack/Upwork pasted `.txt`, plain pasted text.
3. **Extract requests.** Heuristics split client messages into sentences and keep the ones that ask for something (billing admin such as invoices is skipped). `--llm-extract` adds an LLM pass. **Evidence rule:** a request is kept only if its quote appears verbatim in the messages (case and whitespace aside); anything else is dropped and counted.
4. **Consolidate.** A short follow-up that refers back ("set it up and write the first three posts") and near-duplicates from the same sender within 7 days (or 8 messages when there are no timestamps) are merged into one item with several quotes, by content-word overlap. Each item is classified and priced once. Revision requests are only merged with other revisions.
5. **Rules decide first, no model involved:**
   - _Revision requests_ (bigger, smaller, change a colour, move, swap text, font, tweak, adjust, fix a typo) are grouped into rounds (same message, or messages under 30 minutes apart) and compared with the SOW's allowance. Within the allowance: IN-SCOPE. Past it: OUT-OF-SCOPE with the revision clause cited. Exclusions never block a revision of existing work.
   - _Explicit exclusions_ match by phrase: the clause is split into phrases ("Logo or brand design" becomes "Logo" and "brand design"); a short phrase must be fully present in the request, a long one needs two of its content words, so one shared word never matches. A deliverable that explains the request better wins.
   - _Page counter:_ if the SOW says "a five-page website: Home, About, ...", a request that names a listed page ("add a Meet the Team section to the About page") is IN-SCOPE; a request for a page that is not listed ("add a Pricing page", "a sixth page") is OUT-OF-SCOPE once it pushes past the count. If the SOW leaves free slots (four pages, three named), the counter spends them across the conversation first.
6. **The model decides the rest.** It must return IN/OUT/UNCLEAR plus a reason that cites a clause id, as strict JSON. Citations are checked against the real clauses. IN-SCOPE or OUT-OF-SCOPE without a valid clause becomes UNCLEAR; so does anything below `--min-confidence` (default 0.5). **Support check:** an OUT verdict that cites an exclusion must share at least one content word or concept with that exclusion's text (stopwords removed, stemmed); otherwise it becomes UNCLEAR with "model cited exclusion X but the request doesn't mention it". For items the rules already marked OUT, the model is asked only for hours and a title, except revision overages, which always use your rate card's `default_hours`.
7. **Estimate.** Hours come from your `rates.yaml` task match first, then the model's proposed hours, then your default; rounded up to a quarter hour, never below your minimum. Each item gets a plausible range of 0.75x to 1.5x of the point estimate (a stated, arbitrary band). The headline sums only OUT-OF-SCOPE items that cite a clause, each consolidated item once. Only those items get a change-order draft.

Backends: local Ollama (`SCOPEGUARD_OLLAMA_MODEL`, else the first model Ollama lists), then opt-in cloud (`--llm groq` with `GROQ_API_KEY`, `--llm gemini` with `GEMINI_API_KEY`; `--llm auto --allow-cloud` falls back to them), `--llm mock` for offline use, `--llm none` for rules only. The cloud backends follow the documented APIs but are not covered by the automated tests (they need keys).

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

- **LLM judgement is a draft. You decide.** Models misread context, and small local ones are over-confident and guess hours badly (see the sample). Read every row before you say anything to a client.
- Not legal advice. A change-order draft is a polite message, not a contract term. Check your actual agreement.
- The rule layer is lexical and deliberately narrow. It can miss a paraphrased exclusion and, rarely, over-match one. Revision-round counting uses a 30-minute heuristic and cannot know which deliverable a round belongs to; revision wording ("change", "move", "bigger") is a keyword list.
- A request the SOW is silent on is UNCLEAR unless the model cites a clause. The tool will not call something OUT-OF-SCOPE without a clause to point at.
- The extractor can miss requests with no cue words, and consolidation can merge two genuinely separate asks that share a topic. It treats everyone who is not `--client` / `--me` as the client only if it can tell; with no flag it guesses from names in the SOW, or analyses everyone and warns.
- Chat context across messages ("yes, do that") is not resolved. Attachments and images are ignored.
- PDF text extraction needs a text layer. Scanned SOWs need OCR first.
- The eval set is 46 items written by the author. The accuracy table is a regression check, not evidence it will hold on your SOWs.
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

73 tests: parsers (WhatsApp Android and iOS, eml, mbox, txt, Slack/Upwork), clause splitter, revision counter and allowance, phrase-level exclusions, page counter, the exclusion support check, consolidation, evidence rule, mock-LLM classification, change-order math and ranges, HTML and CSV escaping, never-send, redaction, server bind guard and auth, and the eval set (rules-only must get every revision case right).

## Need this wired into your business?

SkynetLabs builds voice agents, n8n back offices and sites that convert → https://skynetjoe.com

## License

MIT, 2026 SkynetLabs. See [LICENSE](LICENSE).
