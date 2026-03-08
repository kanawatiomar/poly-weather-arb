# -*- coding: utf-8 -*-
"""Place top trades from fresh scan."""
import json, time
from pathlib import Path
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds

BUY = "BUY"

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

def load_env():
    env = {}
    p = Path(__file__).parent / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env

def get_client():
    env = load_env()
    c = json.loads((Path(__file__).parent / "creds.json").read_text())
    creds = ApiCreds(
        api_key=c["apiKey"],
        api_secret=c["secret"],
        api_passphrase=c["passphrase"],
    )
    proxy_url = env.get("POLY_PROXY")
    if proxy_url:
        print(f"Proxy: {proxy_url.split('@')[1] if '@' in proxy_url else proxy_url}")
        import httpx
        import py_clob_client.http_helpers.helpers as http_helpers
        # httpx 0.20+ uses proxy= (singular), older uses proxies=
        try:
            http_helpers._http_client = httpx.Client(http2=True, proxy=proxy_url)
        except TypeError:
            http_helpers._http_client = httpx.Client(http2=True, proxies=proxy_url)

    return ClobClient(HOST, chain_id=CHAIN_ID, key=env["POLY_PRIVATE_KEY"], creds=creds)

# ---- FRESH TRADES (from scan 2026-03-07) ----
# Format: (description, token_id, price, shares)
# Spending ~$5 per trade, $20 total
TRADES = [
    # London 11C or below NO (Mar 7) — mkt 79%, model 42% → edge -36.9%, vol $14.9K
    # NO price = 1 - 0.79 = 0.21, spend $5 → 23 shares
    (
        "London <=11C NO (Mar7) | mkt 79% model 42% edge -36.9%",
        "99979041455081823164369993433302475381880697793843143065397688082936756374578",
        0.21, 23
    ),
    # NYC 46-47F NO (Mar 7) — mkt 77.5%, model 43.8% → edge -33.7%, vol $21.8K
    # NO price = 1 - 0.775 = 0.225, spend $5 → 22 shares
    (
        "NYC 46-47F NO (Mar7) | mkt 77.5% model 43.8% edge -33.7%",
        "93204044084944363912025933310484985922400896262986822078011554530560165374933",
        0.225, 22
    ),
    # Miami 80-81F YES (Mar 7) — mkt 15.5%, model 49.1% → edge +33.6%, vol $3.7K
    # YES price = 0.155, spend $5 → 32 shares
    (
        "Miami 80-81F YES (Mar7) | mkt 15.5% model 49.1% edge +33.6%",
        "87964087119811004915238034889817454762992085534143001773375568490476038165789",
        0.155, 32
    ),
    # Buenos Aires 24C YES (Mar 7) — mkt 5.9%, model 52.7% → edge +46.8%, vol $6.6K
    # YES price = 0.059, spend $5 → 84 shares
    (
        "Buenos Aires 24C YES (Mar7) | mkt 5.9% model 52.7% edge +46.8%",
        "57943352243575948576143629791182850731869978765659024022527484200714764282671",
        0.059, 84
    ),
]

def main():
    client = get_client()
    print()

    results = []
    total_cost = 0

    for desc, token_id, price, shares in TRADES:
        cost = price * shares
        total_cost += cost
        print(f"ORDER: {desc}")
        print(f"  Price: ${price:.3f} x {shares} shares = ${cost:.2f}")
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=float(shares),
                side=BUY,
            )
            signed = client.create_order(order_args)
            resp = client.post_order(signed, orderType="GTC")
            print(f"  PLACED: {resp}")
            results.append({"desc": desc, "status": "ok", "cost": cost, "resp": str(resp)})
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"desc": desc, "status": "error", "cost": cost, "error": str(e)})
        time.sleep(1)

    print()
    print("=" * 60)
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"Placed {ok}/{len(results)} orders | Total deployed: ${total_cost:.2f}")
    for r in results:
        tag = "OK  " if r["status"] == "ok" else "FAIL"
        print(f"  [{tag}] {r['desc'][:60]}")

    with open("trades_placed.json", "w") as f:
        json.dump({"ts": time.time(), "trades": results}, f, indent=2)

if __name__ == "__main__":
    main()
