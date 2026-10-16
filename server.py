"""Hearth MCP server (Streamable HTTP, MCP spec 2025-11-25).

Run:  python -m hearth.server            # serves http://127.0.0.1:8765/mcp
Env:  HEARTH_DB        path to SQLite file (default ~/.hearth/hearth.db)
      HEARTH_HOST/PORT bind address (default 127.0.0.1:8765)
      HEARTH_USE_BEDROCK=1  use Amazon Bedrock to read receipts (falls back offline)

NOTE: written against the official `mcp` Python SDK (FastMCP). Pin the version
you test with in requirements.txt. Parts marked VERIFY depend on SDK features
that changed across releases; see README "Known unverified parts".
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from .receipt import extract_with_bedrock, parse_receipt
from .store import Hearth

DB_PATH = Path(os.environ.get("HEARTH_DB", Path.home() / ".hearth" / "hearth.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
store = Hearth(DB_PATH)

mcp = FastMCP(
    "Hearth",
    instructions=(
        "Household memory. Use remember_item when the user says where they put something, "
        "find_item when they ask where something is, track_document / add_from_receipt for "
        "warranties, bills and renewals, and household_brief for 'what needs my attention'. "
        "Prefer short, speakable answers."
    ),
    host=os.environ.get("HEARTH_HOST", "127.0.0.1"),
    port=int(os.environ.get("HEARTH_PORT", "8765")),
)

BRIEF_UI = "ui://hearth/brief"


class WhichItem(BaseModel):
    choice: str = Field(description="Which item did you mean?")


@mcp.tool()
def remember_item(name: str, location: str) -> dict:
    """Remember where a household item is. Say if it was moved from somewhere else."""
    r = store.remember_item(name, location)
    r["spoken"] = (
        f"Got it, {r['name']} is in {r['location']}."
        if not r["moved_from"]
        else f"Updated. {r['name']} moved from {r['moved_from']} to {r['location']}."
    )
    return r


@mcp.tool()
async def find_item(query: str, ctx: Context) -> dict:
    """Find where an item is. If the request matches several items, asks the user which one."""
    matches = store.find_item(query)
    if not matches:
        return {"found": False, "spoken": f"I don't have anything about {query} yet."}

    chosen = matches[0]
    if store.is_ambiguous(matches):
        options = " or ".join(m.name for m in matches[:3])
        try:  # VERIFY: elicitation support differs by client/SDK version
            res = await ctx.elicit(message=f"Which one did you mean: {options}?", schema=WhichItem)
            if res.action == "accept":
                picked = store.find_item(res.data.choice)
                if picked:
                    chosen = picked[0]
        except Exception:  # client without elicitation: list the candidates instead
            return {
                "found": True,
                "ambiguous": True,
                "candidates": [{"name": m.name, "location": m.location} for m in matches[:3]],
                "spoken": "; ".join(f"{m.name} is in {m.location}" for m in matches[:3]) + ".",
            }
    return {
        "found": True,
        "name": chosen.name,
        "location": chosen.location,
        "spoken": f"Your {chosen.name} is in {chosen.location}.",
    }


@mcp.tool()
def save_fact(topic: str, value: str) -> dict:
    """Save shared household info (wifi password, bin day, plumber's number)."""
    r = store.save_fact(topic, value)
    r["spoken"] = f"Saved {r['topic']}."
    return r


@mcp.tool()
def recall_fact(query: str) -> dict:
    """Recall shared household info by topic."""
    hits = store.recall_fact(query)
    if not hits:
        return {"found": False, "spoken": f"I don't have anything saved for {query}."}
    return {"found": True, **hits[0], "spoken": f"{hits[0]['topic']}: {hits[0]['value']}."}


@mcp.tool()
def track_document(
    kind: str,
    name: str,
    due_on: str,
    vendor: Optional[str] = None,
    amount: Optional[str] = None,
    recurs: Optional[str] = None,
) -> dict:
    """Track a warranty, bill, or renewal. kind: warranty|bill|renewal|other.
    due_on is YYYY-MM-DD. recurs: monthly|quarterly|yearly for repeating bills."""
    r = store.track_document(kind, name, due_on, vendor, amount, recurs)
    r["spoken"] = f"I'll keep an eye on {r['name']} due {r['due_on']}."
    return r


@mcp.tool()
def add_from_receipt(text: str) -> dict:
    """Read pasted receipt/warranty/bill text, work out the expiry or due date, and start tracking it.
    If something is missing, returns what to ask the user instead of guessing."""
    use_bedrock = os.environ.get("HEARTH_USE_BEDROCK") == "1"
    parsed = extract_with_bedrock(text) if use_bedrock else parse_receipt(text)
    if parsed["confidence"] != "high" or not parsed.get("due_on"):
        return {
            "tracked": False,
            "missing": parsed.get("missing", []),
            "parsed": parsed,
            "spoken": "I couldn't find the date. When does it expire or fall due?",
        }
    doc = store.track_document(
        parsed.get("kind") or "other",
        parsed.get("name") or "Untitled",
        parsed["due_on"],
        parsed.get("vendor"),
        parsed.get("amount"),
    )
    return {
        "tracked": True,
        **doc,
        "source": parsed["source"],
        "spoken": f"Tracking {doc['name']}. I'll warn you before {doc['due_on']}.",
    }


@mcp.tool()
def upcoming_deadlines(within_days: int = 30) -> dict:
    """List warranties, bills and renewals due soon (or overdue)."""
    items = store.upcoming(within_days)
    return {"count": len(items), "items": items}


@mcp.tool()
def mark_done(doc_id: int) -> dict:
    """Mark a bill/renewal as handled. Repeating bills roll to the next due date."""
    r = store.mark_done(doc_id)
    r["spoken"] = (
        f"Done. Next one is due {r['next_due_on']}." if r["next_due_on"] else f"Marked {r['name']} as done."
    )
    return r


@mcp.tool(meta={"ui": {"resourceUri": BRIEF_UI}})  # VERIFY: `meta=` needs a recent SDK
def household_brief() -> dict:
    """What needs the household's attention right now (overdue, this week, this month)."""
    b = store.brief(date.today())
    b["spoken"] = Hearth.speak_brief(b)
    return b


@mcp.resource(BRIEF_UI, mime_type="text/html;profile=mcp-app")  # VERIFY against MCP Apps spec
def brief_view() -> str:
    """Visual card for screens (Echo Show). Voice-only devices use the `spoken` field."""
    return (Path(__file__).parent / "brief.html").read_text(encoding="utf-8")


def main() -> None:
    mcp.run(transport="streamable-http")  # endpoint: /mcp


if __name__ == "__main__":
    main()
