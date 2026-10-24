# Using scopeguard from n8n

`examples/n8n-workflow.json` watches one Gmail label (one label per client), sends each new email to a local scopeguard server, and posts an **internal** Slack alert listing the _possible extras_ it found. It writes no change-order drafts and sends nothing to the client: scopeguard suggests, you decide. To draft a change order for an item you agree with, run `scopeguard bill <id>` yourself.

```
Gmail Trigger (client label) -> Code -> HTTP POST /analyze -> IF possible extras > 0 -> Slack alert (internal)
```

## 1. Start the server

```bash
scopeguard serve --sow sow.md --rates rates.yaml --client "Dana Okafor" --llm ollama
# listening on http://127.0.0.1:8765
```

- Binds to `127.0.0.1` by default. Binding to any other address is refused unless you pass `--token` (or set `SCOPEGUARD_TOKEN`).
- With a token, every route, `/healthz` included, needs `Authorization: Bearer <token>` (or `X-Scopeguard-Token`).
- The `Host` header must be `localhost`, `127.0.0.1`, the address you bound to, or one passed with `--allowed-host`; anything else gets 403 (this blocks DNS-rebinding from a web page).
- The client must be named (`--client`, or `"client": [...]` in the request) whenever the messages have more than one sender; otherwise the request gets a 400. The match is on whole name tokens.
- The SOW is loaded once at start-up. One SOW per server; run one server per client on different ports.
- Requests are analysed one at a time, so a single local GPU is never hit twice at once. Bodies are capped at 1 MB and are never logged. A connection that stalls for 30 s is dropped.

## 2. Import the workflow

1. n8n: Workflows, Import from file, pick `examples/n8n-workflow.json`.
2. Gmail Trigger: set the label id of the client label.
3. HTTP node: replace `REPLACE_WITH_CLIENT_FULL_NAME` with the client's full name.
4. Add credentials: Gmail OAuth2, Slack, and a Header Auth credential named `Authorization` with value `Bearer <token>` (not needed for loopback with no token; then set the HTTP node's authentication to None).
5. Slack node: set the channel id.
6. Leave the workflow inactive until you have run it once manually and read the output. Add your own execution cap on the trigger before you activate it.

## API

`POST /analyze`

```json
{
  "messages": "<raw text, an RFC822 email, a WhatsApp export, ...>",
  "format": "auto",
  "client": ["Dana Okafor"],
  "tone": "friendly"
}
```

Response: `findings[]` (status, class, confidence, quote, clause, rule, reason, estimate range), `possible_extras[]`, `possible_extras_count`, `headline`. There are no drafts in the response. `GET /healthz` returns `{"ok": true}` and needs the token too when one is set.

## Notes

- Items withdrawn later in the same export ("never mind the shop"), conditional or hypothetical requests, and work you offered for free are never listed as possible extras. The server sees only the messages in each request, so a withdrawal that arrives in a later email will not cancel an earlier alert; re-run `scopeguard analyze` on the whole thread for that.
- If n8n runs on a different machine (for example a VPS), do not expose scopeguard to the internet. Use an SSH tunnel or a private network, plus the token. Your local LLM and SOW stay on your machine.
- The workflow reads email text and the local model reads it too. If you use a cloud model, add `--redact` and read the privacy section of the README first.
