"""Turn pasted receipt / warranty / bill text into a trackable document.

`parse_receipt` is a deterministic offline parser (always available).
`extract_with_bedrock` optionally asks an Amazon Bedrock model first and
falls back to the offline parser if boto3, credentials or the call fail.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from .store import add_months

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")  # day first (India/UK)
_D_MON_Y = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})\b")
_MON_D_Y = re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_PERIOD = re.compile(r"(\d+)[\s-]*(year|yr|month|mo)s?\b[^.\n]{0,20}?warrant", re.I)
_PERIOD2 = re.compile(r"warrant\w*[^.\n]{0,25}?(\d+)[\s-]*(year|yr|month|mo)s?", re.I)
_AMOUNT = re.compile(r"(?:₹|rs\.?|inr|\$|usd)\s?([\d,]+(?:\.\d{1,2})?)", re.I)
_VENDOR = re.compile(r"(?:sold by|seller|vendor|store|merchant|from)\s*[:\-]?\s*([^\n,|]{2,40})", re.I)


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    found: list[date] = []
    for y, m, d in _ISO.findall(text):
        dt = _safe_date(int(y), int(m), int(d))
        if dt:
            found.append(dt)
    for d, m, y in _DMY.findall(text):
        dt = _safe_date(int(y), int(m), int(d))
        if dt:
            found.append(dt)
    for d, mon, y in _D_MON_Y.findall(text):
        if mon.lower() in MONTHS:
            dt = _safe_date(int(y), MONTHS[mon.lower()], int(d))
            if dt:
                found.append(dt)
    for mon, d, y in _MON_D_Y.findall(text):
        if mon.lower() in MONTHS:
            dt = _safe_date(int(y), MONTHS[mon.lower()], int(d))
            if dt:
                found.append(dt)
    return found


def parse_receipt(text: str) -> dict:
    """Best-effort extraction. Returns a dict with a `confidence` of high/low
    and `missing` fields, so the caller knows what to ask the user."""
    dates = find_dates(text)
    purchase = min(dates) if dates else None

    months: Optional[int] = None
    m = _PERIOD.search(text) or _PERIOD2.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        months = n * 12 if unit.startswith(("year", "yr")) else n

    expires: Optional[date] = None
    if purchase and months:
        expires = add_months(purchase, months)

    amount = _AMOUNT.search(text)
    vendor = _VENDOR.search(text)
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")

    kind = "warranty" if months else ("bill" if re.search(r"\b(due|invoice|bill)\b", text, re.I) else "other")
    if kind == "bill" and len(dates) >= 1 and re.search(r"due", text, re.I):
        expires = max(dates)

    missing = [k for k, v in (("due_on", expires), ("name", first_line)) if not v]
    return {
        "kind": kind,
        "name": first_line[:80] or None,
        "vendor": vendor.group(1).strip() if vendor else None,
        "purchase_date": purchase.isoformat() if purchase else None,
        "warranty_months": months,
        "due_on": expires.isoformat() if expires else None,
        "amount": amount.group(0).strip() if amount else None,
        "missing": missing,
        "confidence": "high" if not missing and (months or kind == "bill") else "low",
        "source": "offline-parser",
    }


def extract_with_bedrock(text: str, model_id: str = "amazon.nova-lite-v1:0", region: str = "us-east-1") -> dict:
    """Ask a Bedrock model to extract fields; fall back to the offline parser.

    Never raises: any failure (no boto3, no credentials, bad JSON) returns the
    offline result, tagged with why, so the server keeps working in a demo.
    """
    fallback = parse_receipt(text)
    try:
        import boto3  # optional dependency

        client = boto3.client("bedrock-runtime", region_name=region)
        prompt = (
            "Extract warranty/bill details from this text. Reply with ONLY JSON with keys: "
            "kind (warranty|bill|renewal|other), name, vendor, purchase_date (YYYY-MM-DD or null), "
            "warranty_months (int or null), due_on (YYYY-MM-DD or null), amount (string or null).\n\n" + text
        )
        resp = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 400, "temperature": 0},
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        data = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
        if data.get("due_on"):
            date.fromisoformat(data["due_on"])  # validate
        data.setdefault("missing", [] if data.get("due_on") else ["due_on"])
        data["confidence"] = "high" if data.get("due_on") else "low"
        data["source"] = f"bedrock:{model_id}"
        return data
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all, see docstring
        fallback["source"] = f"offline-parser (bedrock unavailable: {type(exc).__name__})"
        return fallback
