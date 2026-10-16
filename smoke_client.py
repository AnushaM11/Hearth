"""End-to-end smoke test over real Streamable HTTP.

1) python -m hearth.server          (in one terminal)
2) python scripts/smoke_client.py   (in another)

Prints the negotiated protocol version, the tool list, and runs a short scenario.
Exits non-zero if anything is off, so it can gate a demo recording.
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/mcp"
EXPECTED = {
    "remember_item", "find_item", "save_fact", "recall_fact", "track_document",
    "add_from_receipt", "upcoming_deadlines", "mark_done", "household_brief",
}


async def call(session, name, args):
    res = await session.call_tool(name, args)
    payload = res.structuredContent or json.loads(res.content[0].text)
    print(f"\n> {name}({args})\n  {json.dumps(payload, ensure_ascii=False)}")
    return payload


async def main() -> int:
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print("protocol:", init.protocolVersion)
            tools = {t.name for t in (await s.list_tools()).tools}
            missing = EXPECTED - tools
            if missing:
                print("MISSING TOOLS:", missing)
                return 1

            await call(s, "remember_item", {"name": "passport", "location": "blue folder, bedroom shelf"})
            r = await call(s, "find_item", {"query": "where's my passport"})
            assert r["location"] == "blue folder, bedroom shelf"

            r = await call(s, "add_from_receipt", {
                "text": "Samsung Refrigerator\nSold by: Vijay Sales\nInvoice date: 12 Jan 2026\n2 year warranty"})
            assert r["tracked"] and r["due_on"] == "2028-01-12", r

            await call(s, "track_document", {"kind": "bill", "name": "Electricity",
                                             "due_on": "2026-10-12", "recurs": "monthly"})
            b = await call(s, "household_brief", {})
            assert b["counts"]["this_week"] >= 1 or b["counts"]["overdue"] >= 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
