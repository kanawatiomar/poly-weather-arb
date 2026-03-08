# -*- coding: utf-8 -*-
"""Place UFC 326 bets - Holloway, Borralho, Rosas Jr"""
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
        import httpx
        import py_clob_client.http_helpers.helpers as http_helpers
        try:
            http_helpers._http_client = httpx.Client(http2=True, proxy=proxy_url)
        except TypeError:
            http_helpers._http_client = httpx.Client(http2=True, proxies=proxy_url)
    return ClobClient(HOST, chain_id=CHAIN_ID, key=env["POLY_PRIVATE_KEY"], creds=creds)

# UFC 326 Bets - $12 total across 3 fights
# Balance: $12.45 USDC
TRADES = [
    # Max Holloway wins main event - 64.5% favorite, $6 bet
    (
        "Max Holloway wins UFC 326 Main Event @ 64.5%",
        "70680997252637870845060297313707679888294694430159052880614680512560318175930",
        0.645, 9  # $5.81 cost, payout $9.00
    ),
    # Raul Rosas Jr wins - 73.5% favorite, $4 bet  
    (
        "Raul Rosas Jr wins vs Font @ 73.5%",
        "101239751429668060313710372992353945289674063614750487143676549797501063649600",
        0.735, 5  # $3.68 cost, payout $5.00
    ),
    # Caio Borralho wins co-main - 66.5% favorite, $2 bet
    (
        "Caio Borralho wins vs de Ridder @ 66.5%",
        "85359326900765264538832647041057004371172192978060984100891837719760639793294",
        0.665, 3  # $2.00 cost, payout $3.00
    ),
]

def main():
    client = get_client()
    print("=== UFC 326 BETS ===\n")

    results = []
    total_cost = 0

    for desc, token_id, price, shares in TRADES:
        cost = price * shares
        total_cost += cost
        print(f"BET: {desc}")
        print(f"  ${cost:.2f} → payout ${shares:.0f} if wins")
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=float(shares),
                side=BUY,
            )
            signed = client.create_order(order_args)
            resp = client.post_order(signed, orderType="GTC")
            print(f"  PLACED ✓ {resp}")
            results.append({"desc": desc, "status": "ok", "cost": cost, "shares": shares, "resp": str(resp)})
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"desc": desc, "status": "error", "error": str(e)})
        time.sleep(1)

    print(f"\n{'='*50}")
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"Placed {ok}/{len(results)} bets | Total deployed: ${total_cost:.2f}")
    print(f"If all win: ${sum(r.get('shares',0) for r in results if r['status']=='ok'):.0f}")

    with open("ufc_bets.json", "w") as f:
        json.dump({"ts": time.time(), "bets": results}, f, indent=2)

if __name__ == "__main__":
    main()
