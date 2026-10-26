"""Tolerant parsers for client message exports.

Supported: WhatsApp export (Android and iOS), .eml, .mbox, Slack/Upwork exports
(.json or pasted .txt), "Name: text" chat logs and plain pasted text.
"""
from __future__ import annotations

import json
import mailbox
import re
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Iterable, Optional

from .models import Message

MAX_BYTES = 50 * 1024 * 1024

_INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬﻿"), None)
_SPACES = {ord(" "): " ", ord(" "): " "}

# 12/03/2026, 14:05 - Name: text            (Android)
# [12/03/26, 2:05:33 PM] Name: text         (iOS)
_WA = re.compile(
    r"^\[?(?P<d>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+(?P<t>\d{1,2}[:.]\d{2}(?::\d{2})?(?:\s?[APap]\.?[Mm]\.?)?)\]?"
    r"\s*(?:-\s*)?(?P<rest>.*)$")
_SYSTEM_HINTS = (
    "messages and calls are end-to-end encrypted", "<media omitted>", "image omitted", "video omitted",
    "audio omitted", "sticker omitted", "gif omitted", "document omitted", "<attached:", "this message was deleted",
    "you deleted this message", "missed voice call", "missed video call", "security code changed",
    "created group", "added you", "changed the subject", "changed this group",
)
_SLACK_HEADER = re.compile(r"^(?P<name>[A-Z][\w.'\- ]{0,40}?)\s{2,}(?P<t>\d{1,2}:\d{2}(?:\s?[AP]M)?)\s*$")
_NAME_COLON = re.compile(r"^(?:\[?\d{1,2}:\d{2}[^\]\s]*\]?\s+)?(?P<name>[A-Za-z][\w.'\- ]{0,40}?):\s+(?P<text>\S.*)$")


# ---------------------------------------------------------------- helpers
def _clean_line(line: str) -> str:
    return line.translate(_INVISIBLE).translate(_SPACES).rstrip("\r\n")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def _parse_time(t: str) -> tuple[int, int, int]:
    t = t.strip().lower().replace(".", ":", 1) if re.match(r"^\d{1,2}\.\d{2}", t.strip()) else t.strip().lower()
    pm = bool(re.search(r"p\.?m", t))
    am = bool(re.search(r"a\.?m", t))
    nums = [int(x) for x in re.findall(r"\d+", t)[:3]]
    while len(nums) < 3:
        nums.append(0)
    h, m, s = nums
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    return h, m, s


def _date_order(dates: Iterable[str]) -> str:
    """Return 'dmy', 'mdy' or 'ymd' by looking at every date in the export."""
    first_gt12 = second_gt12 = False
    for d in dates:
        parts = [int(x) for x in re.split(r"[./-]", d)]
        if len(parts) != 3:
            continue
        if parts[0] > 31:
            return "ymd"
        if parts[0] > 12:
            first_gt12 = True
        if parts[1] > 12:
            second_gt12 = True
    if second_gt12 and not first_gt12:
        return "mdy"
    return "dmy"


def _build_dt(d: str, t: str, order: str) -> Optional[datetime]:
    try:
        parts = [int(x) for x in re.split(r"[./-]", d)]
        if order == "ymd":
            y, mo, da = parts
        elif order == "mdy":
            mo, da, y = parts
        else:
            da, mo, y = parts
        if y < 100:
            y += 2000
        h, mi, s = _parse_time(t)
        return datetime(y, mo, da, h, mi, s)
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- WhatsApp
def looks_like_whatsapp(text: str) -> bool:
    """Two WhatsApp lines in the first 200, or a single-message export (one non-blank line that is a WhatsApp line)."""
    hits = 0
    nonblank = 0
    for line in text.splitlines():
        clean = _clean_line(line)
        if not clean.strip():
            continue
        nonblank += 1
        if hits < 2 and nonblank <= 200:
            m = _WA.match(clean)
            if m and ":" in m.group("rest"):
                hits += 1
    return hits >= 2 or (hits == 1 and nonblank == 1)


def parse_whatsapp(text: str, source: str = "") -> list[Message]:
    entries: list[dict] = []
    for raw in text.splitlines():
        line = _clean_line(raw)
        m = _WA.match(line)
        if m:
            rest = m.group("rest")
            sm = re.match(r"^(?P<name>[^:]{1,60}?):\s?(?P<text>.*)$", rest)
            if sm and not rest.lower().startswith(_SYSTEM_HINTS):
                entries.append({"d": m.group("d"), "t": m.group("t"), "name": sm.group("name").strip(),
                                "lines": [sm.group("text")]})
            else:
                entries.append({"system": True})  # system notice; swallow its continuation lines
        elif entries and not entries[-1].get("system"):
            entries[-1]["lines"].append(line)
    real = [e for e in entries if not e.get("system")]
    order = _date_order(e["d"] for e in real)
    out: list[Message] = []
    for e in real:
        body = "\n".join(e["lines"]).strip()
        if not body or body.lower().startswith(_SYSTEM_HINTS) or body.lower() in {"<media omitted>", "null"}:
            continue
        out.append(Message(id="", sender=e["name"], text=body, ts=_iso(_build_dt(e["d"], e["t"], order)), source=source))
    return out


# ---------------------------------------------------------------- email
_QUOTE_TAIL = re.compile(r"^(on .{5,200}wrote:|-{2,}\s*original message\s*-{2,}|from:.*\bsent:|_{5,})\s*$", re.I)


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", "", html))


def _strip_quoted(body: str) -> str:
    keep = []
    for line in body.splitlines():
        if _QUOTE_TAIL.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        if line.strip() == "--":
            break
        keep.append(line)
    return "\n".join(keep).strip()


def _message_from_email(em, source: str) -> Optional[Message]:
    body_part = em.get_body(preferencelist=("plain", "html")) if hasattr(em, "get_body") else None
    if body_part is None:
        return None
    content = body_part.get_content()
    if body_part.get_content_type() == "text/html":
        content = _strip_html(content)
    body = _strip_quoted(content)
    if not body:
        return None
    sender = str(em.get("From", "") or "unknown")
    m = re.match(r'^\s*"?([^"<]+?)"?\s*<', sender)
    name = (m.group(1).strip() if m else sender.strip()) or sender
    ts = None
    try:
        dt = parsedate_to_datetime(str(em.get("Date")))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        ts = _iso(dt)
    except (TypeError, ValueError):
        pass
    return Message(id="", sender=name, text=body, ts=ts, source=source, subject=str(em.get("Subject", "") or ""))


def parse_eml(path: Path) -> list[Message]:
    em = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    m = _message_from_email(em, path.name)
    return [m] if m else []


def parse_mbox(path: Path) -> list[Message]:
    out = []
    box = mailbox.mbox(str(path), factory=lambda f: BytesParser(policy=policy.default).parse(f))
    try:
        for em in box:
            m = _message_from_email(em, path.name)
            if m:
                out.append(m)
    finally:
        box.close()
    return out


# ---------------------------------------------------------------- Slack / Upwork / generic
_SENDER_KEYS = ("user_name", "username", "user_real_name", "real_name", "display_name", "author", "from", "sender", "user", "name")
_TEXT_KEYS = ("text", "body", "message", "content", "msg")
_TS_KEYS = ("ts", "timestamp", "createdAt", "created_at", "created", "date", "time", "sentAt", "sent_at")
_LIST_KEYS = ("messages", "items", "data", "results", "conversation", "chat", "comments")


def _name_of(v) -> str:
    if isinstance(v, dict):
        for k in ("name", "real_name", "display_name", "username", "fullName", "full_name", "email"):
            if isinstance(v.get(k), str) and v[k]:
                return v[k]
        return ""
    return str(v) if v is not None else ""


def _ts_of(v) -> Optional[str]:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)) or re.fullmatch(r"\d{9,13}(\.\d+)?", str(v)):
            f = float(v)
            if f > 1e11:
                f /= 1000.0
            return _iso(datetime.fromtimestamp(f, tz=timezone.utc).replace(tzinfo=None))
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return _iso(dt)
    except (ValueError, OverflowError, OSError):
        try:
            dt = parsedate_to_datetime(str(v))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return _iso(dt)
        except (TypeError, ValueError):
            return None


def _find_message_list(obj) -> list:
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return obj
        return []
    if isinstance(obj, dict):
        for k in _LIST_KEYS:
            if k in obj:
                found = _find_message_list(obj[k])
                if found:
                    return found
        for v in obj.values():
            found = _find_message_list(v)
            if found:
                return found
    return []


def parse_json_messages(text: str, source: str = "") -> list[Message]:
    try:
        data = json.loads(text)
    except ValueError:
        return parse_plain(text, source)   # not valid JSON after all: treat as pasted text
    out = []
    for d in _find_message_list(data):
        body = next((d[k] for k in _TEXT_KEYS if isinstance(d.get(k), str) and d[k].strip()), "")
        if not body or d.get("subtype") in {"channel_join", "channel_leave"}:
            continue
        sender = next((_name_of(d[k]) for k in _SENDER_KEYS if _name_of(d.get(k))), "unknown")
        ts = next((_ts_of(d[k]) for k in _TS_KEYS if k in d and _ts_of(d[k])), None)
        out.append(Message(id="", sender=sender, text=body.strip(), ts=ts, source=source))
    return out


def parse_slack_txt(text: str, source: str = "") -> list[Message]:
    """Slack copy-paste: 'Name  10:42 AM' header followed by message lines."""
    out: list[Message] = []
    cur: Optional[dict] = None
    for raw in text.splitlines():
        line = _clean_line(raw)
        m = _SLACK_HEADER.match(line.strip())
        if m:
            if cur and cur["lines"]:
                out.append(Message(id="", sender=cur["name"], text="\n".join(cur["lines"]).strip(), source=source))
            cur = {"name": m.group("name").strip(), "lines": []}
        elif cur is not None and line.strip():
            cur["lines"].append(line.strip())
    if cur and cur["lines"]:
        out.append(Message(id="", sender=cur["name"], text="\n".join(cur["lines"]).strip(), source=source))
    return out


def parse_name_colon(text: str, source: str = "") -> list[Message]:
    """'Name: text' lines (Upwork/Slack txt exports, chat logs). Continuation lines append."""
    out: list[Message] = []
    for raw in text.splitlines():
        line = _clean_line(raw)
        m = _NAME_COLON.match(line)
        if m:
            out.append(Message(id="", sender=m.group("name").strip(), text=m.group("text").strip(), source=source))
        elif out and line.strip():
            out[-1].text += "\n" + line.strip()
    return out


def parse_plain(text: str, source: str = "") -> list[Message]:
    body = text.strip()
    return [Message(id="", sender="client", text=body, source=source)] if body else []


# ---------------------------------------------------------------- dispatch
def detect_format(path: Path, text: Optional[str] = None) -> str:
    ext = path.suffix.lower()
    if ext == ".eml":
        return "eml"
    if ext == ".mbox":
        return "mbox"
    if ext == ".json":
        return "json"
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    head = text.lstrip()
    if head.startswith("From ") and re.search(r"(?m)^(From|Subject|Date):", text[:2000]):
        return "mbox"
    if head[:1] in "[{":
        try:
            json.loads(text)
            return "json"
        except ValueError:
            pass
    if looks_like_whatsapp(text):
        return "whatsapp"
    if sum(1 for ln in text.splitlines() if _SLACK_HEADER.match(_clean_line(ln).strip())) >= 2:
        return "slack"
    if sum(1 for ln in text.splitlines() if _NAME_COLON.match(_clean_line(ln))) >= 2:
        return "chat"
    return "plain"


def parse_file(path: str | Path, fmt: str = "auto") -> list[Message]:
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise ValueError(f"{p.name} is larger than 50 MB; refusing to parse it")
    if fmt == "auto":
        fmt = detect_format(p)
    if fmt == "eml":
        msgs = parse_eml(p)
    elif fmt == "mbox":
        msgs = parse_mbox(p)
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        if fmt == "auto":
            fmt = detect_format(p, text)
        if fmt == "whatsapp":
            msgs = parse_whatsapp(text, p.name)
        elif fmt in ("json", "upwork"):
            msgs = parse_json_messages(text, p.name)
        elif fmt == "slack":
            msgs = parse_slack_txt(text, p.name)
        elif fmt == "chat":
            msgs = parse_name_colon(text, p.name)
        elif fmt in ("txt", "plain"):
            msgs = parse_plain(text, p.name)
        else:
            raise ValueError(f"unknown format {fmt!r}")
    return msgs


def parse_text_blob(text: str, fmt: str = "auto", source: str = "api") -> list[Message]:
    """Parse messages given as a string (used by `scopeguard serve`)."""
    if fmt == "auto":
        if re.match(r"(?i)\s*(From |From:|To:|Return-Path:|Received:|MIME-Version:|Delivered-To:|Message-ID:)", text) \
                and re.search(r"(?m)^Subject:", text[:4000]) and re.search(r"(?m)^(From|Date):", text[:4000]):
            fmt = "eml"
        else:
            fmt = detect_format(Path("x.txt"), text)
    if fmt == "eml":
        em = BytesParser(policy=policy.default).parsebytes(text.encode("utf-8"))
        m = _message_from_email(em, source)
        return [m] if m else []
    if fmt == "whatsapp":
        return parse_whatsapp(text, source)
    if fmt in ("json", "upwork"):
        return parse_json_messages(text, source)
    if fmt == "slack":
        return parse_slack_txt(text, source)
    if fmt == "chat":
        return parse_name_colon(text, source)
    return parse_plain(text, source)


def load_messages(paths: Iterable[str | Path], fmt: str = "auto") -> list[Message]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(x for x in p.iterdir() if x.is_file() and x.suffix.lower() in {".txt", ".eml", ".mbox", ".json"})
        else:
            files.append(p)
    msgs: list[Message] = []
    for f in files:
        msgs += parse_file(f, fmt)
    for i, m in enumerate(msgs, 1):
        m.id = f"M{i}"
    return msgs
