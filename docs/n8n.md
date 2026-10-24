# Using scopeguard from n8n

`examples/n8n-workflow.json` watches one Gmail label (one label per client), sends each new email to a local scopeguard server, and posts a **draft** alert to Slack plus a **draft** in your own Gmail. Nothing is ever sent to the client.

```
Gmail Trigger (client label) -> Code -> HTTP POST /analyze -> IF out-of-scope > 0 -> Slack alert (internal)
                                                                                  -> Gmail "create draft" (your mailbox)
```

## 1. Start the server

```bash
scopeguard serve --sow sow.md --rates rates.yaml --client "Dana Okafor" --llm ollama
# listening on http://127.0.0.1:8765
```

- Binds to `127.0.0.1` by default. Binding to any other address is refused unless you pass `--token` (or set `SCOPEGUARD_TOKEN`).
- With a token, every request needs `Authorization: Bearer <token>` (or `X-Scopeguard-Token`).
- The SOW is loaded once at start-up. One SOW per server; run one server per client on different ports.
- Requests are analysed one at a time, so a single local GPU is never hit twice at once.
- Request bodies are capped at 1 MB and are never logged. A connection that stalls for 30 s is dropped.
- The `Host` header must be `localhost`, `127.0.0.1`, the address you bound to, or one passed with `--allowed-host`; anything else gets 403 (this blocks DNS-rebinding from a web page).

## 2. Import the workflow

1. n8n: Workflows, Import from file, pick `examples/n8n-workflow.json`.
2. Gmail Trigger: set the label id of the client label.
3. Add credentials: Gmail OAuth2, Slack, and a Header Auth credential named `Authorization` with value `Bearer <token>` for the HTTP node (not needed for loopback-only without a token; then switch the HTTP node's authentication to None).
4. Slack node: set the channel id. Gmail draft node: set your own address.
5. Leave the workflow inactive until you have run it once manually and read the output.

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

Response: `findings[]` (class, confidence, quote, clause, rule, reason, estimate), `drafts{id: markdown}`, `out_of_scope_count`. `GET /healthz` returns `{"ok": true}` and needs the token too when one is set.

## Notes

- If n8n runs on a different machine (for example a VPS), do not expose scopeguard to the internet. Use an SSH tunnel or a private network, plus the token. Your local LLM and SOW stay on your machine.
- The workflow reads email text and the local model reads it too. If you use a cloud model, add `--redact` and read the privacy section of the README first.
- Per the n8n loop-safety habit: add your own execution cap on the trigger before you activate it.
