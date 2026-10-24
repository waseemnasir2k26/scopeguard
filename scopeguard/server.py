"""`scopeguard serve`: tiny local HTTP API for n8n and friends. Analyses only; sends nothing."""
from __future__ import annotations

import hmac
import ipaddress
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .changeorder import draft
from .messages import parse_text_blob
from .models import OUT_OF_SCOPE
from .outputs import to_dict
from .pipeline import Options, analyze

MAX_BODY = 1_000_000


class BindError(ValueError):
    pass


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str, token: str | None) -> None:
    """Refuse non-loopback binds unless a token is required."""
    if not is_loopback(host) and not token:
        raise BindError(f"Refusing to bind to {host}: non-loopback addresses require --token "
                        "(or SCOPEGUARD_TOKEN). Use 127.0.0.1 for local-only access.")


def make_server(host: str, port: int, sow, sow_text: str, opts: Options, token: str | None = None) -> ThreadingHTTPServer:
    check_bind(host, token)
    lock = threading.Lock()   # one analysis at a time: protects a single local GPU

    class H(BaseHTTPRequestHandler):
        server_version = "scopeguard"

        def log_message(self, fmt, *args):  # never log bodies (client data)
            pass

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            if not token:
                return True
            got = self.headers.get("X-Scopeguard-Token", "")
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                got = auth[7:].strip()
            return hmac.compare_digest(got.encode(), token.encode())

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, {"ok": True, "clauses": len(sow.clauses), "model": opts.llm.label()})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/analyze":
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if n <= 0 or n > MAX_BODY:
                return self._send(413 if n > MAX_BODY else 400, {"error": "body must be 1..1000000 bytes"})
            try:
                req = json.loads(self.rfile.read(n).decode("utf-8"))
                text = req["messages"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("messages must be a non-empty string")
            except (ValueError, KeyError, UnicodeDecodeError) as e:
                return self._send(400, {"error": f"bad request: {e}"})
            client = req.get("client") or []
            client = [client] if isinstance(client, str) else [str(c) for c in client]
            tone = req.get("tone") if req.get("tone") in ("friendly", "neutral", "firm") else opts.tone
            msgs = parse_text_blob(text, str(req.get("format", "auto")))
            o = Options(llm=opts.llm, client=client or opts.client, me=opts.me, rates=opts.rates, tone=tone,
                        redact=opts.redact, llm_extract=opts.llm_extract, min_confidence=opts.min_confidence)
            try:
                with lock:
                    res = analyze(sow, messages=msgs, opts=o, sow_text=sow_text)
            except Exception as e:  # report, never crash the server
                return self._send(500, {"error": f"analysis failed: {type(e).__name__}"})
            out = to_dict(res)
            drafts = {f.id: draft(f, opts.rates, tone, sow.revision_limit) for f in res.findings if f.cls == OUT_OF_SCOPE}
            out["drafts"] = drafts
            out["out_of_scope_count"] = len(drafts)
            out["note"] = "Drafts only. scopeguard never sends anything."
            self._send(200, out)

    return ThreadingHTTPServer((host, port), H)
