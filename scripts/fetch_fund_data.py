"""
Resolves a curated watchlist of fund names to their AMFI scheme codes via
mfapi.in (free, no key, wraps official AMFI NAV data), pulls each fund's
historical NAV, computes trailing 1Y/3Y/5Y CAGR, and writes the result to
funds.json at the repo root.

Design principle, same as fetch_market_data.py: never let one fund's failure
kill the whole run, and never overwrite a good previous entry with nothing —
each fund's own last-good snapshot is preserved if today's fetch for it fails.
"""
import requests
import json
import datetime
import sys
import os

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "funds.json")
SEARCH_URL = "https://api.mfapi.in/mf/search"
SCHEME_URL = "https://api.mfapi.in/mf/{code}"

# Curated watchlist. Edit this list to change which funds are tracked —
# the script does the rest (resolving to scheme code + computing returns).
WATCHLIST = {
    "small": [
        "Bandhan Small Cap Fund Direct Growth",
        "Invesco India Smallcap Fund Direct Growth",
        "Nippon India Small Cap Fund Direct Growth",
    ],
    "mid": [
        "HDFC Mid Cap Opportunities Fund Direct Growth",
        "Motilal Oswal Midcap Fund Direct Growth",
        "Edelweiss Mid Cap Fund Direct Growth",
    ],
    "flexi": [
        "Bank of India Flexi Cap Fund Direct Growth",
        "Quant Flexi Cap Fund Direct Growth",
        "HDFC Flexi Cap Fund Direct Growth",
    ],
    "thematic": [
        "ICICI Prudential Infrastructure Fund Direct Growth",
        "Aditya Birla Sun Life Manufacturing Equity Fund Direct Growth",
    ],
    "liquid": [
        "SBI Liquid Fund Direct Growth",
        "Axis Liquid Fund Direct Growth",
        "HDFC Liquid Fund Direct Growth",
    ],
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; sensex-signal-fund-fetch/1.0)"}


def resolve_scheme_code(name):
    """Search mfapi.in for the fund name, prefer a Direct + Growth match, skip IDCW/dividend variants."""
    resp = requests.get(SEARCH_URL, params={"q": name}, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None, None

    def score(entry):
        n = entry["schemeName"].lower()
        s = 0
        if "direct" in n:
            s += 2
        if "growth" in n:
            s += 2
        if "idcw" in n or "dividend" in n or "bonus" in n:
            s -= 5
        return s

    best = max(results, key=score)
    return best["schemeCode"], best["schemeName"]


def cagr_from_nav(nav_series, years):
    """nav_series: list of (date, nav) newest-first. Returns CAGR % or None if not enough history."""
    if not nav_series:
        return None
    latest_date, latest_nav = nav_series[0]
    target_date = latest_date - datetime.timedelta(days=int(years * 365.25))

    # find the nav entry closest to target_date (search within a 10-day window either side)
    closest = min(nav_series, key=lambda x: abs((x[0] - target_date).days))
    if abs((closest[0] - target_date).days) > 10:
        return None  # not enough history for this horizon

    old_nav = closest[1]
    if old_nav <= 0:
        return None
    return (pow(latest_nav / old_nav, 1 / years) - 1) * 100


def fetch_fund(name):
    code, resolved_name = resolve_scheme_code(name)
    if not code:
        raise ValueError(f"No scheme found for '{name}'")

    resp = requests.get(SCHEME_URL.format(code=code), headers=HEADERS, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    nav_series = []
    for row in payload.get("data", []):
        try:
            d = datetime.datetime.strptime(row["date"], "%d-%m-%Y").date()
            nav_series.append((d, float(row["nav"])))
        except (ValueError, KeyError):
            continue
    nav_series.sort(key=lambda x: x[0], reverse=True)

    if not nav_series:
        raise ValueError(f"No NAV history returned for '{resolved_name}' ({code})")

    return {
        "name": resolved_name,
        "schemeCode": code,
        "nav": nav_series[0][1],
        "navDate": nav_series[0][0].isoformat(),
        "return1y": cagr_from_nav(nav_series, 1),
        "return3y": cagr_from_nav(nav_series, 3),
        "return5y": cagr_from_nav(nav_series, 5),
    }


def main():
    existing = {}
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    result = {"asOf": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "sleeves": {}}
    any_success = False

    for sleeve, names in WATCHLIST.items():
        funds = []
        existing_sleeve = {f["name"]: f for f in existing.get("sleeves", {}).get(sleeve, [])}
        for name in names:
            try:
                fund = fetch_fund(name)
                funds.append(fund)
                any_success = True
                print(f"[ok] {sleeve}: {fund['name']} -> 5Y {fund['return5y']}")
            except Exception as e:
                print(f"[warn] {sleeve}: failed to fetch '{name}': {e}", file=sys.stderr)
                # fall back to previous good entry for this fund name, if any
                fallback = existing_sleeve.get(name)
                if fallback:
                    funds.append(fallback)
        result["sleeves"][sleeve] = funds

    if not any_success and not existing:
        # total failure on a totally fresh run — nothing to fall back to
        sys.exit(1)

    with open(DATA_PATH, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
