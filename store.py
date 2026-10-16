"""Household memory store.

Pure standard library (sqlite3) so the logic can be tested without any
network access. The MCP server in server.py is a thin layer over this.

Three kinds of memory:
  * items      - where things are ("passport -> blue folder, bedroom shelf")
  * facts      - shared household info ("wifi password", "bin day")
  * documents  - warranties, bills, renewals with a due/expiry date, so the
                 assistant can warn *before* something lapses
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,          -- normalised name
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    previous_location TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- warranty | bill | renewal | other
    name TEXT NOT NULL,
    vendor TEXT,
    due_on TEXT NOT NULL,              -- ISO date: expiry or due date
    amount TEXT,
    recurs TEXT,                       -- monthly | quarterly | yearly | NULL
    notes TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

DOC_KINDS = ("warranty", "bill", "renewal", "other")
RECURRENCES = {"monthly": 1, "quarterly": 3, "yearly": 12}

_STOP = {"the", "a", "an", "my", "our", "of", "to", "is", "in", "on", "for", "where", "what"}
MATCH_THRESHOLD = 0.55
AMBIGUITY_GAP = 0.08


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(text.split())


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t not in _STOP}


def match_score(query: str, candidate: str) -> float:
    """0..1 similarity: token containment blended with character similarity."""
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    qt, ct = _tokens(query), _tokens(candidate)
    containment = len(qt & ct) / len(qt) if qt else 0.0
    ratio = difflib.SequenceMatcher(None, q, c).ratio()
    return max(ratio, 0.95 * containment)


def add_months(d: date, months: int) -> date:
    """Add calendar months, clamping the day (31 Jan + 1 month -> 28/29 Feb)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


@dataclass
class Match:
    name: str
    location: str
    score: float


class Hearth:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------------------------------------------------------------- items
    def remember_item(self, name: str, location: str) -> dict:
        key = _norm(name)
        if not key:
            raise ValueError("item name is empty")
        row = self.conn.execute("SELECT location FROM items WHERE key=?", (key,)).fetchone()
        prev = row["location"] if row else None
        if prev is not None and prev != location:
            self.conn.execute(
                "UPDATE items SET name=?, location=?, previous_location=?, updated_at=? WHERE key=?",
                (name.strip(), location.strip(), prev, self._now(), key),
            )
        elif prev is None:
            self.conn.execute(
                "INSERT INTO items(key,name,location,updated_at) VALUES(?,?,?,?)",
                (key, name.strip(), location.strip(), self._now()),
            )
        self.conn.commit()
        return {"name": name.strip(), "location": location.strip(), "moved_from": prev if prev != location else None}

    def find_item(self, query: str, limit: int = 5) -> list[Match]:
        """Ranked matches above the threshold, best first."""
        rows = self.conn.execute("SELECT name, location FROM items").fetchall()
        scored = [Match(r["name"], r["location"], match_score(query, r["name"])) for r in rows]
        scored = [m for m in scored if m.score >= MATCH_THRESHOLD]
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:limit]

    @staticmethod
    def is_ambiguous(matches: list[Match]) -> bool:
        """True when the top two matches are too close to pick one safely."""
        return len(matches) >= 2 and (matches[0].score - matches[1].score) < AMBIGUITY_GAP

    def forget_item(self, name: str) -> bool:
        cur = self.conn.execute("DELETE FROM items WHERE key=?", (_norm(name),))
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------- facts
    def save_fact(self, topic: str, value: str) -> dict:
        key = _norm(topic)
        if not key:
            raise ValueError("topic is empty")
        self.conn.execute(
            "INSERT INTO facts(key,topic,value,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET topic=excluded.topic, value=excluded.value, updated_at=excluded.updated_at",
            (key, topic.strip(), value.strip(), self._now()),
        )
        self.conn.commit()
        return {"topic": topic.strip(), "value": value.strip()}

    def recall_fact(self, query: str, limit: int = 3) -> list[dict]:
        rows = self.conn.execute("SELECT topic, value FROM facts").fetchall()
        scored = [(match_score(query, r["topic"]), r) for r in rows]
        scored = [(s, r) for s, r in scored if s >= MATCH_THRESHOLD]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"topic": r["topic"], "value": r["value"], "score": round(s, 2)} for s, r in scored[:limit]]

    # --------------------------------------------------------- documents
    def track_document(
        self,
        kind: str,
        name: str,
        due_on: date | str,
        vendor: Optional[str] = None,
        amount: Optional[str] = None,
        recurs: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        kind = kind.lower().strip()
        if kind not in DOC_KINDS:
            raise ValueError(f"kind must be one of {DOC_KINDS}")
        if recurs:
            recurs = recurs.lower().strip()
            if recurs not in RECURRENCES:
                raise ValueError(f"recurs must be one of {tuple(RECURRENCES)}")
        due = due_on if isinstance(due_on, date) else date.fromisoformat(due_on)
        cur = self.conn.execute(
            "INSERT INTO documents(kind,name,vendor,due_on,amount,recurs,notes,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (kind, name.strip(), vendor, due.isoformat(), amount, recurs, notes, self._now()),
        )
        self.conn.commit()
        return {"id": cur.lastrowid, "kind": kind, "name": name.strip(), "due_on": due.isoformat()}

    @staticmethod
    def urgency(days_left: int) -> str:
        if days_left < 0:
            return "overdue"
        if days_left <= 7:
            return "this_week"
        if days_left <= 30:
            return "this_month"
        return "later"

    def upcoming(self, within_days: int = 30, today: Optional[date] = None) -> list[dict]:
        today = today or date.today()
        horizon = today + timedelta(days=within_days)
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE done=0 AND due_on<=? ORDER BY due_on", (horizon.isoformat(),)
        ).fetchall()
        out = []
        for r in rows:
            left = (date.fromisoformat(r["due_on"]) - today).days
            out.append(
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "name": r["name"],
                    "vendor": r["vendor"],
                    "due_on": r["due_on"],
                    "amount": r["amount"],
                    "days_left": left,
                    "urgency": self.urgency(left),
                }
            )
        return out

    def mark_done(self, doc_id: int) -> dict:
        """Mark a bill/renewal handled. Recurring ones roll forward instead of closing."""
        r = self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if r is None:
            raise KeyError(f"no document with id {doc_id}")
        if r["recurs"]:
            nxt = add_months(date.fromisoformat(r["due_on"]), RECURRENCES[r["recurs"]])
            self.conn.execute("UPDATE documents SET due_on=? WHERE id=?", (nxt.isoformat(), doc_id))
            self.conn.commit()
            return {"id": doc_id, "name": r["name"], "next_due_on": nxt.isoformat()}
        self.conn.execute("UPDATE documents SET done=1 WHERE id=?", (doc_id,))
        self.conn.commit()
        return {"id": doc_id, "name": r["name"], "next_due_on": None}

    # ------------------------------------------------------------- brief
    def brief(self, today: Optional[date] = None) -> dict:
        """Everything the household should know right now, grouped by urgency."""
        today = today or date.today()
        groups: dict[str, list[dict]] = {"overdue": [], "this_week": [], "this_month": []}
        for d in self.upcoming(within_days=30, today=today):
            groups[d["urgency"]].append(d)
        recent = self.conn.execute(
            "SELECT name, location FROM items ORDER BY updated_at DESC, id DESC LIMIT 5"
        ).fetchall()
        return {
            "as_of": today.isoformat(),
            **groups,
            "recent_items": [{"name": r["name"], "location": r["location"]} for r in recent],
            "counts": {k: len(v) for k, v in groups.items()},
        }

    @staticmethod
    def speak_brief(b: dict) -> str:
        """Short spoken summary for a voice-only device."""
        parts = []
        if b["overdue"]:
            parts.append("Overdue: " + ", ".join(d["name"] for d in b["overdue"]))
        if b["this_week"]:
            parts.append(
                "Due this week: " + ", ".join(f'{d["name"]} in {d["days_left"]} days' for d in b["this_week"])
            )
        if b["this_month"]:
            parts.append(f'{len(b["this_month"])} more coming up this month')
        return ". ".join(parts) + "." if parts else "Nothing needs your attention right now."
