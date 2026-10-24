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

    def cite(self) -> str:
        return f"{self.id} (SOW {self.ref})" if self.ref else self.id


@dataclass
class Sow:
    clauses: list[Clause]
    revision_limit: Optional[int] = None
    revision_clause: Optional[str] = None

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


@dataclass
class Finding:
    id: str
    request: Request
    cls: str
    confidence: float
    clause_id: Optional[str] = None
    clause_text: str = ""
    clause_ref: str = ""
    reason: str = ""
    title: str = ""
    rule: Optional[str] = None        # explicit_exclusion | revision_limit | None
    llm_class: Optional[str] = None
    estimate: Optional[dict] = None
    draft_file: str = ""
    notes: list[str] = field(default_factory=list)
