from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Task:
    match: list[str]
    hours: float | None = None
    fixed: float | None = None
    label: str = ""


@dataclass
class RateCard:
    hourly_rate: float = 50.0
    currency: str = "USD"
    minimum_hours: float = 0.5
    default_hours: float = 2.0
    tasks: list[Task] = field(default_factory=list)
    placeholder: bool = False   # True when no rates.yaml was supplied


def _num(v, name: str, lo: float = 0.0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < lo:
        raise ValueError(f"rates.yaml: {name} must be a number >= {lo}")
    return float(v)


def load_rates(path: str | Path | None) -> RateCard:
    if not path:
        return RateCard(placeholder=True)
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("rates.yaml must be a mapping")
    card = RateCard(
        hourly_rate=_num(data.get("hourly_rate", 50), "hourly_rate"),
        currency=str(data.get("currency", "USD")).upper()[:6],
        minimum_hours=_num(data.get("minimum_hours", 0.5), "minimum_hours"),
        default_hours=_num(data.get("default_hours", 2), "default_hours"),
    )
    for i, t in enumerate(data.get("tasks") or []):
        if not isinstance(t, dict) or not t.get("match"):
            raise ValueError(f"rates.yaml: tasks[{i}] needs a 'match' list")
        m = t["match"] if isinstance(t["match"], list) else [t["match"]]
        task = Task(match=[str(x).lower() for x in m],
                    hours=_num(t["hours"], f"tasks[{i}].hours") if "hours" in t else None,
                    fixed=_num(t["fixed"], f"tasks[{i}].fixed") if "fixed" in t else None,
                    label=str(t.get("label") or m[0]))
        if task.hours is None and task.fixed is None:
            raise ValueError(f"rates.yaml: tasks[{i}] needs 'hours' or 'fixed'")
        card.tasks.append(task)
    return card


SAMPLE = """# scopeguard rate card. Every number here is yours to edit.
hourly_rate: 60          # per hour
currency: USD
minimum_hours: 0.5       # smallest billable unit for a change order
default_hours: 2         # used when neither a task match nor the LLM gives hours
tasks:                   # optional: first match wins (keywords are matched against the request)
  - match: [blog, article]
    hours: 6
    label: Blog setup
  - match: [translate, translation, spanish, french]
    fixed: 450
    label: Translation of the site
  - match: [logo, branding]
    hours: 5
    label: Logo work
"""
