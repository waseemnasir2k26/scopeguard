"""`scopeguard serve`: tiny local HTTP API for n8n and friends. Analyses only; sends nothing."""
from __future__ import annotations

import hmac
import ipaddress
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .changeorder import headline
from .messages import parse_text_blob
from .outputs import to_dict
from .ledger import LedgerCorrupt
from .pipeline import ClientError, Options, analyze
from .store import StoreCorrupt
from .thread import analyze_thread

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


def make_server(host: str, port: int, sow, sow_text: str, opts: Options, token: str | None = None,
                allowed_hosts: tuple = (), data_dir: str | None = None) -> ThreadingHTTPServer:
    check_bind(host, token)
    allowed = {"localhost", "127.0.0.1", "[::1]", "::1", host.lower()} | {h.lower() for h in allowed_hosts}
    lock = threading.Lock()   # one analysis at a time: protects a single local GPU

    class H(BaseHTTPRequestHandler):
        server_version = "scopeguard"
        timeout = 30          # read timeout per connection, so a stalled client cannot hold a thread forever

        def _host_ok(self) -> bool:
            """Reject requests whose Host header is not ours (blocks DNS-rebinding from a web page)."""
            h = (self.headers.get("Host") or "").lower().strip()
            name = h.rsplit(":", 1)[0] if not h.endswith("]") and ":" in h else h
            return name in allowed or name.strip("[]") in allowed

        def log_message(self, fmt, *args):  # never log bodies (client data)
            pass

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.close_connection = True
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
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            if self.path == "/healthz":
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                return self._send(200, {"ok": True})        # deliberately minimal: no model, no SOW details
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
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
                    if data_dir:        # accumulate: the whole thread is analysed, with the ledger applied
                        res, folder = analyze_thread(data_dir, o.client, msgs, sow, sow_text, o)
                    else:               # stateless (library use): only this request's messages are seen
                        res, folder = analyze(sow, messages=msgs, opts=o, sow_text=sow_text), None
            except ClientError as e:
                return self._send(400, {"error": str(e)})
            except (StoreCorrupt, LedgerCorrupt) as e:
                return self._send(409, {"error": str(e)})
            except Exception as e:  # report, never crash the server
                return self._send(500, {"error": f"analysis failed: {type(e).__name__}"})
            out = to_dict(res)
            extras = [f for f in out["findings"] if f["status"] == "extra"]
            out["possible_extras"] = extras
            out["possible_extras_count"] = len(extras)
            out["headline"] = headline(res.findings, opts.rates.currency)
            out["thread"] = ({"folder": str(folder), "messages": res.meta.get("thread_messages"),
                              "new_messages": res.meta.get("new_messages")} if folder else None)
            out["note"] = ("scopeguard suggests; you decide. No drafts are written by the server: "
                           "run `scopeguard bill <id>` locally for the items you agree with.")
            self._send(200, out)

    return ThreadingHTTPServer((host, port), H)
