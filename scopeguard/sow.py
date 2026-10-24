"""Load a SOW (.md/.txt/.pdf/.docx) and split it into numbered clauses."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import Clause, Sow
from .textutil import NUMBER_WORDS

MAX_BYTES = 20 * 1024 * 1024


def load_text(path: str | Path) -> str:
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise ValueError(f"{p.name} is larger than 20 MB; refusing to parse it")
    ext = p.suffix.lower()
    if ext == ".docx":
        return _docx_text(p)
    if ext == ".pdf":
        return _pdf_text(p)
    return p.read_text(encoding="utf-8", errors="replace")


def _docx_text(p: Path) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(p) as z:
        if z.getinfo("word/document.xml").file_size > MAX_BYTES:
            raise ValueError("document.xml too large")
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    for para in root.iter(ns + "p"):
        text = "".join(t.text or "" for t in para.iter(ns + "t"))
        style = para.find(f"{ns}pPr/{ns}pStyle")
        sval = style.get(ns + "val", "") if style is not None else ""
        if sval.lower().startswith("heading") or sval.lower() == "title":
            text = "## " + text
        elif para.find(f"{ns}pPr/{ns}numPr") is not None:
            text = "- " + text
        lines.append(text)
    return "\n".join(lines)


def _pdf_text(p: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("PDF input needs the optional extra: pip install 'scopeguard[pdf]'") from e
    reader = PdfReader(str(p))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


_HEADING_MD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+([A-Z][^.!?]{2,60})\s*:?\s*$")
_BULLET = re.compile(r"^\s*(?:[-*•●]|\d+(?:\.\d+)+[.)]?|\d+[.)]|[a-z][.)])\s+(.*\S)")
_NUM_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+)+|\d+(?=[.)]))[.)]?\s+")

_EXCL_HEAD = re.compile(r"exclusion|not included|out of scope|excluded|out-of-scope", re.I)
_REV_HEAD = re.compile(r"revision|change request|feedback|rounds", re.I)
_TIME_HEAD = re.compile(r"timeline|schedule|milestone|delivery|timing|dates?\b", re.I)
_DELIV_HEAD = re.compile(r"deliverable|scope|included|in scope|features|requirements|pages|work", re.I)
_EXCL_TEXT = re.compile(
    r"\b(not included|excluded|out of scope|does not include|do not include|not part of|"
    r"will not (?:include|cover|provide)|is not covered|are not covered|not covered|separate(?:ly)? (?:quoted|billed|scoped))\b", re.I)
_REV_TEXT = re.compile(r"\brounds?\s+of\s+(?:\w+\s+)?(?:revisions?|changes|edits|feedback)|\brevisions?\b|\brevision rounds?\b", re.I)
_TIME_TEXT = re.compile(r"\b(week|weeks|days?|deadline|milestone|launch date|kick-?off|within \d+|business days?)\b", re.I)

_REV_LIMIT = re.compile(
    r"\b(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\(\d+\)\s*)?"
    r"(?:\w+\s+){0,2}?(?:rounds?|revisions?|revision rounds?)\b", re.I)


def _kind(heading: str, text: str) -> str:
    if _EXCL_TEXT.search(text):
        return "exclusion"
    if _EXCL_HEAD.search(heading):
        return "exclusion"
    if _REV_TEXT.search(text) or _REV_HEAD.search(heading):
        return "revision"
    if _TIME_HEAD.search(heading) or (_TIME_TEXT.search(text) and re.search(r"\d", text)
                                      and not _DELIV_HEAD.search(heading)):
        return "timeline"
    if _DELIV_HEAD.search(heading):
        return "deliverable"
    return "other"


def _clean(s: str) -> str:
    s = re.sub(r"[*_`]{1,3}", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_sow(text: str) -> Sow:
    clauses: list[Clause] = []
    heading = ""
    started = False
    para: list[str] = []

    def add(item: str, ref: str = "", bullet: bool = False) -> None:
        item = _clean(item)
        if len(item.split()) < (1 if bullet else 3) or not re.search(r"[A-Za-z]{3}", item):
            return
        clauses.append(Clause(id=f"C{len(clauses) + 1}", text=item,
                              kind=_kind(heading, item), section=heading, ref=ref))

    def flush() -> None:
        if not para:
            return
        block = " ".join(para)
        para.clear()
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", block):
            add(sent)

    n_headings = sum(1 for ln in text.splitlines() if _HEADING_MD.match(ln))
    has_heading = n_headings > 0
    skip_block = False
    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        m = _HEADING_MD.match(line)
        if m:
            flush()
            heading = _clean(m.group(2))
            started = True
            skip_block = len(m.group(1)) == 1 and n_headings > 1   # document title: its blurb is not a clause
            continue
        if skip_block or (has_heading and not started):
            continue   # title block / preamble is not a clause
        m = _NUMBERED_HEADING.match(line)
        if m and not line.rstrip().endswith("."):
            flush()
            heading = _clean(m.group(2))
            continue
        if line.rstrip().endswith(":") and len(line.split()) <= 8 and not _BULLET.match(line):
            flush()
            heading = _clean(line.rstrip(": "))
            continue
        b = _BULLET.match(line)
        if b:
            flush()
            nm = _NUM_PREFIX.match(line)
            add(b.group(1), nm.group(1) if nm else "", bullet=True)
            continue
        if line.startswith((" ", "\t")) and clauses and not para and not _HEADING_MD.match(line):
            # indented continuation of a bullet
            clauses[-1].text = _clean(clauses[-1].text + " " + line)
            clauses[-1].kind = _kind(heading, clauses[-1].text)
            continue
        para.append(line.strip())
    flush()

    sow = Sow(clauses=clauses)
    for c in clauses:
        if c.kind == "revision":
            m = _REV_LIMIT.search(c.text)
            if m:
                tok = m.group("n").lower()
                n = int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok)
                if n:
                    sow.revision_limit, sow.revision_clause = n, c.id
                    break
    return sow


def load_sow(path: str | Path) -> Sow:
    return parse_sow(load_text(path))
