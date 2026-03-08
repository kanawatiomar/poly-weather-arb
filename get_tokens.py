# -*- coding: utf-8 -*-
"""Get correct CLOB token IDs for our target markets."""
import requests, json

HEADERS = {"User-Agent": "Mozilla/5.0"}
BASE = "https://gamma-api.polymarket.com"

TARGETS = [
    ("london-march-7", "highest-temperature-in-london-on-march-7-2026", "11 or below", "No"),
    ("nyc-march-7",    "highest-temperature-in-nyc-on-march-7-2026",    "46-47",       "No"),
    ("miami-march-7",  "highest-temperature-in-miami-on-march-7-2026",  "80-81",       "Yes"),
    ("bsas-march-7",   "highest-temperature-in-buenos-aires-on-march-7-2026", "24", "Yes"),
]

for label, slug, match, signal in TARGETS:
    r = requests.get(f"{BASE}/events?slug={slug}", headers=HEADERS, timeout=10)
    if not r.ok or not r.json():
        print(f"{label}: NOT FOUND")
        continue
    event = r.json()[0]
    for m in event.get("markets", []):
        q = m.get("question", "").lower()
        if match.lower() in q:
            outcomes = json.loads(m.get("outcomes", "[]"))
            clob_ids = json.loads(m.get("clobTokenIds", "[]"))
            prices   = json.loads(m.get("outcomePrices", "[]"))
            print(f"\n{label} — {m['question'][:70]}")
            for o, tid, p in zip(outcomes, clob_ids, prices):
                marker = " <-- USE THIS" if o == signal else ""
                print(f"  {o}: price={p} token={tid}{marker}")
            break
