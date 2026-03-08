"""
auto_trade.py — Polymarket Weather Arb Auto Trader
Reads scan_results.json, picks top March 8+ edges, places BUY orders.
"""

import os, json, sys
from pathlib import Path
from datetime import date, timedelta


BASE = Path(__file__).parent

TRADE_DOLLARS = 3.0   # $ per trade
MAX_TRADES    = 2     # max positions to open
MIN_EDGE      = 0.20  # only trade if edge > 20%
MIN_DATE      = (date.today() + timedelta(days=1)).isoformat()  # tomorrow+ (skip only today's markets)


def load_env():
    env = {}
    ef = BASE / ".env"
    if ef.exists():
        for line in ef.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    env = load_env()
    private_key = env.get("POLY_PRIVATE_KEY") or os.environ.get("POLY_PRIVATE_KEY")
    proxy_url   = env.get("POLY_PROXY")       or os.environ.get("POLY_PROXY")

    if not private_key:
        print("ERROR: POLY_PRIVATE_KEY not found in .env")
        sys.exit(1)
    if not proxy_url:
        print("WARNING: No POLY_PROXY set — using direct connection (requires VPN active)")
        proxy_url = None

    creds_file = BASE / "creds.json"
    if not creds_file.exists():
        print("ERROR: creds.json not found. Run: python trader.py auth")
        sys.exit(1)
    with open(creds_file) as f:
        creds = json.load(f)

    results_file = BASE / "scan_results.json"
    if not results_file.exists():
        print("ERROR: scan_results.json not found. Run scanner first.")
        sys.exit(1)
    with open(results_file) as f:
        results = json.load(f)

    # Filter: future dates only + min edge
    opps = [
        o for o in results["opportunities"]
        if o.get("date", "") >= MIN_DATE
        and abs(o.get("edge_pct", 0)) >= MIN_EDGE
    ]
    opps.sort(key=lambda x: abs(x["edge_pct"]), reverse=True)

    print("[AutoTrader] Polymarket Weather Arb")
    print(f"  Scan date : {results.get('date')}")
    print(f"  Min date  : {MIN_DATE}  (skipping today's resolved markets)")
    print(f"  Eligible  : {len(opps)} opportunities with >{MIN_EDGE:.0%} edge")
    print(f"  Trading   : top {MAX_TRADES} at ${TRADE_DOLLARS:.0f} each")
    print()

    if not opps:
        print("No eligible opportunities found. Re-run scanner.")
        sys.exit(0)

    top = opps[:MAX_TRADES]

    # --- Patch httpx BEFORE importing ClobClient ---
    import httpx
    import py_clob_client.http_helpers.helpers as http_helpers
    client_kwargs = dict(http2=True, timeout=30.0, verify=False)
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
    http_helpers._http_client = httpx.Client(**client_kwargs)

    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs

    api_creds = ApiCreds(
        api_key=creds["apiKey"],
        api_secret=creds["secret"],
        api_passphrase=creds["passphrase"],
    )

    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        creds=api_creds,
        signature_type=0,
    )

    log = []

    for i, opp in enumerate(top, 1):
        question = opp["question"]
        signal   = opp["signal"]       # "YES" or "NO"
        edge     = opp["edge_pct"]
        token_id = opp["token_id"]
        yes_price = float(opp["yes_price"])
        mdate    = opp.get("date", "")

        # Buy price:
        #   YES signal -> we buy YES token at yes_price
        #   NO signal  -> we buy NO token at (1 - yes_price)
        if signal == "YES":
            price = round(max(yes_price, 0.01), 4)
        else:
            price = round(max(1.0 - yes_price, 0.01), 4)

        # Number of shares we can buy with TRADE_DOLLARS
        raw_size = TRADE_DOLLARS / price
        size = max(1.0, round(raw_size, 1))
        cost = price * size

        print(f"[{i}/{MAX_TRADES}] BUY {signal}  |  edge {edge:+.1%}  |  date {mdate}")
        print(f"  Q   : {question[:70]}")
        print(f"  Price: ${price:.4f}  |  Shares: {size}  |  Cost: ~${cost:.2f}")
        print(f"  Token: {token_id[:50]}...")

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=float(size),
                side="BUY",
            )
            signed = client.create_order(order_args)
            resp   = client.post_order(signed, orderType="GTC")

            if isinstance(resp, dict):
                status   = resp.get("status", "?")
                order_id = resp.get("orderID", "")
                error_msg = resp.get("errorMsg", "")
            else:
                status, order_id, error_msg = str(resp), "", ""

            if error_msg:
                print(f"  -> WARN : {error_msg}")
            print(f"  -> Status : {status}")
            if order_id:
                print(f"  -> OrderID: {order_id}")

            log.append({
                "question" : question,
                "signal"   : signal,
                "date"     : mdate,
                "price"    : price,
                "size"     : size,
                "cost"     : cost,
                "token_id" : token_id,
                "status"   : status,
                "order_id" : order_id,
                "error"    : error_msg,
            })

        except Exception as e:
            print(f"  -> ERROR: {e}")
            log.append({
                "question": question,
                "signal"  : signal,
                "date"    : mdate,
                "error"   : str(e),
            })

        print()

    # Save log
    log_file = BASE / "trade_log.json"
    with open(log_file, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[AutoTrader] Done. Log saved to {log_file}")


if __name__ == "__main__":
    main()
