"""LLM backends: local Ollama (default), opt-in Groq/Gemini, and an offline mock.

Every backend implements chat(system, user) -> str. json_call() wraps it with strict JSON
parsing and one retry. Nothing here ever sends a message to a client.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

from .models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR
from .textutil import overlap


class LLMError(RuntimeError):
    pass


def extract_json(text: str):
    """Pull the first JSON object out of a model reply (tolerates code fences and chatter)."""
    if not isinstance(text, str):
        raise ValueError("no text")
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found")


class Backend:
    name = "base"
    model = ""
    is_cloud = False

    def chat(self, system: str, user: str, schema: Optional[dict] = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def json_call(self, system: str, user: str, schema: Optional[dict] = None) -> dict:
        last = ""
        for attempt in range(2):
            u = user if attempt == 0 else user + "\n\nYour previous reply was not valid JSON. Reply with ONE JSON object only."
            last = self.chat(system, u, schema)
            try:
                data = extract_json(last)
                if isinstance(data, dict):
                    return data
            except ValueError:
                pass
        raise LLMError(f"{self.name}: model did not return valid JSON (got: {last[:120]!r})")

    def label(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise LLMError(f"HTTP {e.code} from {url.split('?')[0]}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"cannot reach {url.split('?')[0]}: {e}") from None


def _ollama_host() -> str:
    h = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
    if not h.startswith("http"):
        h = "http://" + h
    return h.rstrip("/")


class OllamaLLM(Backend):
    name = "ollama"

    def __init__(self, model: Optional[str] = None, timeout: float = 900.0):
        self.host = _ollama_host()
        self.timeout = timeout
        self.model = model or os.environ.get("SCOPEGUARD_OLLAMA_MODEL") or self._first_model()

    def _first_model(self) -> str:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=3) as r:
                models = json.loads(r.read().decode("utf-8")).get("models", [])
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise LLMError(f"Ollama is not reachable at {self.host}: {e}") from None
        if not models:
            raise LLMError("Ollama is running but has no models. Run: ollama pull qwen3:8b")
        return models[0]["name"]

    def chat(self, system: str, user: str, schema: Optional[dict] = None) -> str:
        body = {"model": self.model, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": 0, "num_ctx": 8192}}
        if schema:
            body["format"] = schema
        else:
            body["format"] = "json"
        if self.model.startswith("gpt-oss"):
            body["think"] = "low"
        data = _post(self.host + "/api/chat", body, {}, self.timeout)
        return (data.get("message") or {}).get("content", "")


class GroqLLM(Backend):
    name = "groq"
    is_cloud = True

    def __init__(self, model: Optional[str] = None, timeout: float = 90.0):
        self.key = os.environ.get("GROQ_API_KEY", "")
        if not self.key:
            raise LLMError("GROQ_API_KEY is not set")
        self.model = model or os.environ.get("SCOPEGUARD_GROQ_MODEL", "llama-3.3-70b-versatile")
        self.timeout = timeout

    def chat(self, system, user, schema=None):
        data = _post("https://api.groq.com/openai/v1/chat/completions",
                     {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                     {"Authorization": "Bearer " + self.key}, self.timeout)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("unexpected Groq response shape") from None


class GeminiLLM(Backend):
    name = "gemini"
    is_cloud = True

    def __init__(self, model: Optional[str] = None, timeout: float = 90.0):
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        if not self.key:
            raise LLMError("GEMINI_API_KEY is not set")
        self.model = model or os.environ.get("SCOPEGUARD_GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout = timeout

    def chat(self, system, user, schema=None):
        data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                     {"systemInstruction": {"parts": [{"text": system}]},
                      "contents": [{"role": "user", "parts": [{"text": user}]}],
                      "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
                     {"x-goog-api-key": self.key}, self.timeout)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("unexpected Gemini response shape") from None


# ------------------------------------------------------------------ mock
_VAGUE = re.compile(r"\b(feel|feels|premium|modern|pop|nicer|better|professional|vibe|fresh|wow|polish|more)\b", re.I)


class MockLLM(Backend):
    """Deterministic offline stand-in: lexical overlap between the request and each clause.
    Good for tests, demos and CI. It is not a substitute for a real model."""
    name = "mock"
    model = "lexical-overlap"

    def chat(self, system: str, user: str, schema: Optional[dict] = None) -> str:
        if "EXTRACT_JSON:" in user:
            from .extract import heuristic_requests
            from .models import Message
            payload = json.loads(user.split("EXTRACT_JSON:", 1)[1].strip())
            msgs = [Message(id=m["message_id"], sender=m["sender"], text=m["text"]) for m in payload["messages"]]
            reqs = heuristic_requests(msgs)
            return json.dumps({"requests": [{"message_id": r.message_id, "quote": r.quote} for r in reqs]})
        payload = json.loads(user.split("REQUEST_JSON:", 1)[1].strip())
        quote = payload["quote"]
        scored = [(overlap(quote, c["text"]), c) for c in payload["clauses"]]
        deliv = max((s for s in scored if s[1]["kind"] in ("deliverable", "other")), default=(0, None), key=lambda x: x[0])
        excl = max((s for s in scored if s[1]["kind"] == "exclusion"), default=(0, None), key=lambda x: x[0])
        rev = next((c for c in payload["clauses"] if c["kind"] == "revision"), None)
        if excl[0] >= 2 and excl[0] > deliv[0]:
            out = dict(**{"class": OUT_OF_SCOPE}, clause_id=excl[1]["id"], confidence=0.85,
                       reason="The request overlaps an explicit exclusion.", estimated_hours=2.0)
        elif deliv[0] >= 2:
            out = dict(**{"class": IN_SCOPE}, clause_id=deliv[1]["id"], confidence=min(0.9, 0.5 + 0.1 * deliv[0]),
                       reason="The request overlaps a listed deliverable.", estimated_hours=None)
        elif payload.get("looks_like_revision") and rev:
            out = dict(**{"class": IN_SCOPE}, clause_id=rev["id"], confidence=0.6,
                       reason="Reads like a revision of existing work.", estimated_hours=None)
        elif _VAGUE.search(quote):
            out = dict(**{"class": UNCLEAR}, clause_id=None, confidence=0.35,
                       reason="Too vague to map to a clause.", estimated_hours=None)
        else:
            out = dict(**{"class": OUT_OF_SCOPE}, clause_id=None, confidence=0.55,
                       reason="No clause covers this request.", estimated_hours=2.0)
        out["title"] = " ".join(quote.rstrip("?.! ").split()[:8])
        return json.dumps(out)


def ollama_available() -> bool:
    try:
        with urllib.request.urlopen(_ollama_host() + "/api/tags", timeout=2) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("models"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def make_backend(kind: str = "auto", allow_cloud: bool = False, model: Optional[str] = None) -> Backend:
    kind = (kind or "auto").lower()
    if kind == "mock":
        return MockLLM()
    if kind == "ollama":
        return OllamaLLM(model)
    if kind == "groq":
        return GroqLLM(model)
    if kind == "gemini":
        return GeminiLLM(model)
    if kind != "auto":
        raise LLMError(f"unknown --llm {kind!r} (use auto, ollama, groq, gemini or mock)")
    if ollama_available():
        return OllamaLLM(model)
    if allow_cloud:
        for cls, env in ((GroqLLM, "GROQ_API_KEY"), (GeminiLLM, "GEMINI_API_KEY")):
            if os.environ.get(env):
                return cls(model)
        raise LLMError("--allow-cloud is set but neither GROQ_API_KEY nor GEMINI_API_KEY is in the environment")
    raise LLMError("No local Ollama found. Start Ollama, or pass --llm groq|gemini (cloud, opt-in), or --llm mock for an offline demo.")
