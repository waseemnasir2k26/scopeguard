from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

IN_SCOPE = "IN-SCOPE"
OUT_OF_SCOPE = "OUT-OF-SCOPE"
UNCLEAR = "UNCLEAR"
CLASSES = (IN_SCOPE, OUT_OF_SCOPE, UNCLEAR)


@dataclass
class Clause:
    id: str            # C1, C2 ...
    text: str
    kind: str          # deliverable | exclusion | revision | timeline | other
    section: str = ""
    ref: str = ""      # the SOW's own numbering, e.g. "3.2", if any
    exceptions: list = field(default_factory=list)   # carve-outs: "except one launch post, which is included"
    exception_included: bool = False                 # the clause says the carved-out item IS included
    core: str = ""     # text without the exception part (what the exclusion actually covers)

    def core_text(self) -> str:
        return self.core or self.text

    def cite(self) -> str:
        return f"{self.id} (SOW {self.ref})" if self.ref else self.id


@dataclass
class Sow:
    clauses: list[Clause]
    revision_limit: Optional[int] = None
    revision_clause: Optional[str] = None
    page_count: Optional[int] = None          # "a five-page website"
    page_names: list = field(default_factory=list)   # "Home, About, Services, ..." (lowercase)
    page_clause: Optional[str] = None
    caps: list = field(default_factory=list)          # numeric limits: {"unit","n","clause"}
    client_supplied: list = field(default_factory=list)   # concept tokens the client provides (copy, logo files, photos)
    client_clause: Optional[str] = None

    def by_id(self, cid: str) -> Optional[Clause]:
        for c in self.clauses:
            if c.id == cid:
                return c
        return None


@dataclass
class Message:
    id: str
    sender: str
    text: str
    ts: Optional[str] = None   # ISO-8601 when known
    source: str = ""
    subject: str = ""


@dataclass
class Request:
    id: str
    message_id: str
    sender: str
    ts: Optional[str]
    quote: str
    is_revision: bool = False
    revision_round: int = 0
    extra_quotes: list = field(default_factory=list)   # consolidated duplicates / split parts
    message_ids: list = field(default_factory=list)
    act: str = "request"          # request | retracted | hypothetical | conditional | sarcastic
    withdrawn_by: str = ""        # a later retraction that references this request
    conflict: str = ""            # a retraction in the same message contradicts this request
    hedged: str = ""              # a hedge right after it ("no pressure at all", "if the budget allows")

    def all_quotes(self) -> list:
        return [self.quote] + list(self.extra_quotes)

    def text(self) -> str:
        return " ".join(self.all_quotes())


@dataclass
class Finding:
    id: str
    request: Request
    cls: str
    confidence: float
    clause_id: Optional[str] = None
    clause_text: str = ""
    clause_ref: str = ""
    clause_kind: str = ""
    reason: str = ""
    title: str = ""
    rule: Optional[str] = None        # explicit_exclusion | revision_limit | None
    llm_class: Optional[str] = None
    estimate: Optional[dict] = None
    draft_file: str = ""
    notes: list[str] = field(default_factory=list)
    round_log: list = field(default_factory=list)     # counted revision rounds, for the hedge in drafts
    status: str = ""          # extra | call | in | withdrawn | included_by_you | dismissed | billed
    fingerprint: str = ""     # stable id across re-runs (sender + time + wording)
    free_by: str = ""         # a freelancer message offering this for free
