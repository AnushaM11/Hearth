# Hearth: a household memory for Alexa+

Built for the **Alexa+ track** of *Build, Ship, Shape: Amazon Developer Hackathon* (plus the **AWS Builder** and **Open Source** mini challenges).

Ask Alexa+ where you put something, what the wifi password is, or what's about to expire. Hearth remembers for the whole household and **warns you before** a warranty, bill or renewal lapses.

> "Alexa, I put the passport in the blue folder on the bedroom shelf."
> "Alexa, where are my scissors?"
> "Alexa, add this receipt." *(paste/read it out; Hearth works out the warranty end date)*
> "Alexa, what needs my attention?"

## Why it is more than a note-taker

| Rubric criterion | What Hearth does |
|---|---|
| Tech implementation | Self-hosted MCP server, Streamable HTTP, spec 2025-11-25. Elicitation when a request is ambiguous ("laptop charger or phone charger?"). Tool results carry a `spoken` field for voice and structured data for screens. |
| Design | Voice-first answers, plus an MCP App "household brief" card for Echo Show (`ui://hearth/brief`). |
| Potential impact | Real household pain: lost items, missed warranty claims, late bills. No hardware needed. |
| Quality of the idea | State that persists across sessions; proactive deadlines instead of passive lookup; recurring bills roll forward when marked done. |

## Layout

```
hearth/store.py     memory engine (stdlib sqlite3): items, facts, documents, urgency, brief
hearth/receipt.py   receipt -> warranty/bill extraction (offline parser + optional Amazon Bedrock)
hearth/server.py    MCP server (FastMCP, Streamable HTTP)
hearth/brief.html   MCP App view for screens
tests/              26 unit tests (stdlib unittest, no network needed)
scripts/smoke_client.py   end-to-end check over real Streamable HTTP
```

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 -m unittest discover -s tests -t .      # logic tests
python -m hearth.server                         # http://127.0.0.1:8765/mcp
python scripts/smoke_client.py                  # in a second terminal
```

Optional Bedrock receipt reading (needs AWS credentials with Bedrock model access):

```bash
HEARTH_USE_BEDROCK=1 python -m hearth.server
```

If Bedrock isn't reachable it silently falls back to the offline parser and reports which one it used.

## Connect to Alexa+

Alexa+ needs a **remote HTTPS URL** for your MCP server (a localhost URL won't work). For the hackathon, a tunnel is quickest (for example `cloudflared tunnel --url http://127.0.0.1:8765`), or deploy to AWS.

Then use the Alexa AI CLI (see the Alexa+ MCP QuickStart on developer.amazon.com):

```bash
alexa-ai configure
alexa-ai new mcp --name "Hearth" --locale en-US --mcp-server-url "https://<your-public-host>/mcp" --category <category>
alexa-ai deploy        # redeploy whenever tools change
```

Check the QuickStart for valid `--category` values, or let the Add-on Agent Skill pick one for you.

## Known unverified parts (be honest in the submission)

These were written against the SDK docs but **not executed** in the environment where the code was drafted (package index was blocked), so run `scripts/smoke_client.py` first and fix anything it flags:

- `server.py`: `ctx.elicit(...)` signature and the `meta={"ui": ...}` argument on `@mcp.tool` vary across `mcp` releases.
- `brief.html`: the MCP Apps message handshake is minimal; compare with the `@modelcontextprotocol/ext-apps` docs.
- Alexa+ account linking (OAuth 2.1) is not implemented; the store is a single household.

The memory engine (`store.py`, `receipt.py`) is fully covered by the unit tests.

## License

MIT, see `LICENSE`.
