# -*- coding: utf-8 -*-
"""Fetch token IDs + place trades in one shot."""
import requests, json, time
from pathlib import Path
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds

BUY = "BUY"
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
HEADERS = {"User-Agent": "Mozilla/5.0"}
BASE = "https://gamma-api.polymarket.com"

# Best March 8 plays from scan
# (slug, question_contains, outcome, price_cap, shares, description)
TARGETS = [
    (
        "highest-temperature-in-chicago-on-march-8-2026",
        "56-57", "Yes", 0.15, 40,
        "Chicago 56-57F YES | forecast 57.4F | edge +38%"
    ),
    (
        "highest-temperature-in-miami-on-march-8-2026",
        "80-81", "Yes", 0.20, 30,
        "Miami 80-81F YES | forecast 81.2F | edge +33%"
    ),
    (
        "highest-temperature-in-atlanta-on-march-8-2026",
        "68-69", "Yes", 0.18, 35,
        "Atlanta 68-69F YES | forecast 68.6F | edge +34.5%"
    ),
    (
        "highest-temperature-in-buenos-aires-on-march-8-2026",
        "24", "Yes", 0.10, 60,
        "Buenos Aires 24C YES | forecast 24.9C | edge +47%"
    ),
]

def load_env():
    env = {}
    p = Path(__file__).parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env

def get_client():
    env = load_env()
    c = json.loads((Path(__file__).parent / "creds.json").read_text())
    creds = ApiCreds(api_key=c["apiKey"], api_secret=c["secret"], api_passphrase=c["passphrase"])
    proxy = env.get("POLY_PROXY")
    if proxy:
        print(f"Proxy: {proxy.split('@')[1] if '@' in proxy else proxy}")
        import httpx
        import py_clob_client.http_helpers.helpers as hh
        try:
            hh._http_client = httpx.Client(http2=True, proxy=proxy)
        except TypeError:
            hh._http_client = httpx.Client(http2=True, proxies=proxy)
    return ClobClient(HOST, chain_id=CHAIN_ID, key=env["POLY_PRIVATE_KEY"], creds=creds)

def resolve_token(slug, q_contains, outcome):
    r = requests.get(f"{BASE}/events?slug={slug}", headers=HEADERS, timeout=10)
    if not r.ok or not r.json():
        return None, None
    for m in r.json()[0].get("markets", []):
        q = m.get("question", "").lower()
        if q_contains.lower() in q:
            outcomes = json.loads(m.get("outcomes", "[]"))
            clob_ids = json.loads(m.get("clobTokenIds", "[]"))
            prices   = json.loads(m.get("outcomePrices", "[]"))
            if outcome in outcomes:
                idx = outcomes.index(outcome)
                return clob_ids[idx], float(prices[idx])
    return None, None

def main():
    client = get_client()
    print()
    results = []

    for slug, q_match, outcome, price_cap, shares, desc in TARGETS:
        print(f"Resolving: {desc}")
        token_id, live_price = resolve_token(slug, q_match, outcome)

        if not token_id:
            print(f"  SKIP: token not found for {slug}/{q_match}")
            results.append({"desc": desc, "status": "skip", "reason": "token not found"})
            continue

        if live_price > price_cap:
            print(f"  SKIP: price moved to {live_price:.1%} (cap {price_cap:.1%}) — edge gone")
            results.append({"desc": desc, "status": "skip", "reason": f"price {live_price:.1%} > cap {price_cap:.1%}"})
            continue

        # Use live price with small buffer
        order_price = round(min(live_price + 0.01, price_cap), 3)
        cost = order_price * shares
        print(f"  Token: {token_id[:30]}...")
        print(f"  Live price: {live_price:.1%} | Order price: {order_price:.3f} | {shares} shares = ${cost:.2f}")

        try:
            signed = client.create_order(OrderArgs(
                token_id=token_id,
                price=order_price,
                size=float(shares),
                side=BUY,
            ))
            resp = client.post_order(signed, orderType="GTC")
            print(f"  PLACED: {resp}")
            results.append({"desc": desc, "status": "ok", "cost": cost, "token": token_id, "price": order_price, "shares": shares})
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"desc": desc, "status": "error", "error": str(e)})
        time.sleep(1)

    print()
    print("=" * 65)
    ok = sum(1 for r in results if r["status"] == "ok")
    total = sum(r.get("cost", 0) for r in results if r["status"] == "ok")
    print(f"Placed {ok}/{len(results)} orders | Total deployed: ${total:.2f}")
    for r in results:
        tag = "OK  " if r["status"] == "ok" else ("SKIP" if r["status"] == "skip" else "FAIL")
        print(f"  [{tag}] {r['desc'][:60]}")
    with open("trades_placed.json", "w") as f:
        json.dump({"ts": time.time(), "trades": results}, f, indent=2)

if __name__ == "__main__":
    main()
