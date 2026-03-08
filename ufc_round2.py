import json, time, sys
from pathlib import Path
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds

BUY = "BUY"
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

def load_env():
    env = {}
    p = Path(".env")
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env

env = load_env()
c = json.loads(Path("creds.json").read_text())
creds = ApiCreds(api_key=c["apiKey"], api_secret=c["secret"], api_passphrase=c["passphrase"])
proxy_url = env.get("POLY_PROXY")
if proxy_url:
    import httpx
    import py_clob_client.http_helpers.helpers as http_helpers
    try:
        http_helpers._http_client = httpx.Client(http2=True, proxy=proxy_url)
    except TypeError:
        http_helpers._http_client = httpx.Client(http2=True, proxies=proxy_url)

client = ClobClient(HOST, chain_id=CHAIN_ID, key=env["POLY_PRIVATE_KEY"], creds=creds)

# Remaining budget: $37.20 ($50 - $12.80 already placed)
# Rosas Jr 73.5% - highest confidence -> $19.85 (27 shares)
# Holloway 64.5% - main event -> $11.61 (18 shares)
# Borralho 66.5% - co-main -> $5.32 (8 shares)
# Total = $36.78

TRADES = [
    ("Rosas Jr wins [round 2]", "101239751429668060313710372992353945289674063614750487143676549797501063649600", 0.735, 27),
    ("Holloway wins [round 2]", "70680997252637870845060297313707679888294694430159052880614680512560318175930", 0.645, 18),
    ("Borralho wins [round 2]", "85359326900765264538832647041057004371172192978060984100891837719760639793294", 0.665, 8),
]

results = []
total_cost = 0
total_payout = 0

sys.stdout.write("=== UFC 326 ROUND 2 BETS ===\n\n")
for desc, token_id, price, shares in TRADES:
    cost = price * shares
    total_cost += cost
    total_payout += shares
    sys.stdout.write("BET: %s | $%.2f -> $%d payout\n" % (desc, cost, shares))
    sys.stdout.flush()
    try:
        order_args = OrderArgs(token_id=token_id, price=price, size=float(shares), side=BUY)
        signed = client.create_order(order_args)
        resp = client.post_order(signed, orderType="GTC")
        sys.stdout.write("  PLACED: %s\n" % str(resp))
        results.append({"desc": desc, "status": "ok", "cost": cost, "shares": shares})
    except Exception as e:
        sys.stdout.write("  ERROR: %s\n" % str(e))
        results.append({"desc": desc, "status": "error", "error": str(e)})
    time.sleep(1)

ok = sum(1 for r in results if r["status"] == "ok")
sys.stdout.write("\n%d/%d placed | Total deployed: $%.2f\n" % (ok, len(results), total_cost))
sys.stdout.write("If all win: $%d\n" % total_payout)
