"""
Fetches BSE Sensex level + 52-week high (Yahoo Finance, no key needed) and
Nifty 50 P/E ratio (NSE India, no key needed but flaky from cloud IPs), then
writes/merges the result into data.json at the repo root.

Design principle: never write a null over a previously-good value. If a
source fails today, the last known good figure is kept and the corresponding
*FetchOk flag is set to false so the PWA can show it as stale rather than
silently wrong.
"""
import requests
import json
import datetime
import sys
import os

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EBSESN"
NSE_HOME_URL = "https://www.nseindia.com"
NSE_INDICES_URL = "https://www.nseindia.com/api/allIndices"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_sensex():
    """Returns (current_level, fifty_two_week_high) or (None, None) on failure."""
    resp = requests.get(YAHOO_URL, params={"range": "1y", "interval": "1d"},
                         headers=BROWSER_HEADERS, timeout=15)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]

    current = meta.get("regularMarketPrice")
    high52w = meta.get("fiftyTwoWeekHigh")

    if not high52w:
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        high52w = max(closes) if closes else None

    return current, high52w


def fetch_nifty_pe():
    """Returns P/E as float, or None on failure (NSE frequently blocks non-IN cloud IPs)."""
    session = requests.Session()
    session.get(NSE_HOME_URL, headers=BROWSER_HEADERS, timeout=10)  # seed cookies
    resp = session.get(NSE_INDICES_URL, headers=BROWSER_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    for idx in data.get("data", []):
        if idx.get("index") == "NIFTY 50":
            pe = idx.get("pe")
            if pe:
                return float(pe)
    return None


def main():
    existing = {}
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    sensex, high52w = None, None
    try:
        sensex, high52w = fetch_sensex()
    except Exception as e:
        print(f"[warn] Sensex fetch failed: {e}", file=sys.stderr)

    pe = None
    try:
        pe = fetch_nifty_pe()
    except Exception as e:
        print(f"[warn] Nifty P/E fetch failed: {e}", file=sys.stderr)

    result = {
        "sensex": sensex if sensex is not None else existing.get("sensex"),
        "high52w": high52w if high52w is not None else existing.get("high52w"),
        "pe": pe if pe is not None else existing.get("pe"),
        "asOf": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "sensexFetchOk": sensex is not None,
        "peFetchOk": pe is not None,
    }

    with open(DATA_PATH, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(json.dumps(result, indent=2))

    # Non-zero exit only if BOTH sources failed on a day with no prior data at all —
    # a single-source miss with a fallback value present is not a hard failure.
    if result["sensex"] is None and result["pe"] is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
