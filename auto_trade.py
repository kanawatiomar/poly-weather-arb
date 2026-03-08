"""
auto_trade.py — Polymarket Weather Arb Auto Trader
Reads scan_results.json, picks top edges, places BUY orders.
Sizing: Half-Kelly criterion — bet proportional to edge, capped at MAX_BET.
"""

import os, json, sys
from pathlib import Path
from datetime import date, timedelta, datetime


BASE = Path(__file__).parent

# ── Kelly sizing config ────────────────────────────────────────────────────
KELLY_FRAC    = 0.5   # Half-Kelly (safer than full Kelly, less variance)
MIN_BET       = 1.0   # $ minimum per trade
MAX_BET       = 8.0   # $ maximum per trade (cap Kelly to avoid overbet)
MAX_TRADES    = 3     # max positions to open per run
MIN_EDGE      = 0.20  # only trade if edge > 20%
MIN_DATE      = (date.today() + timedelta(days=1)).isoformat()

# ── Liquidity + concentration config ──────────────────────────────────────
MIN_ASK_DEPTH     = 3.0    # $ of asks available at/near price (skip thin books)
MAX_CITY_EXPOSURE = 10.0   # $ max already deployed in any single city
MAX_DATE_EXPOSURE = 8.0    # $ max in any single date
MAX_PORTFOLIO_PCT = 0.35   # max % of bankroll in a single position

def get_live_bankroll(private_key, creds):
    """Fetch live USDC.e balance from Polymarket."""
    try:
        import httpx, py_clob_client.http_helpers.helpers as h
        h._http_client = httpx.Client(http2=True, timeout=20, verify=False)
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
        api_creds = ApiCreds(api_key=creds["apiKey"], api_secret=creds["secret"], api_passphrase=creds["passphrase"])
        client = ClobClient("https://clob.polymarket.com", key=private_key, chain_id=137, creds=api_creds, signature_type=0)
        result = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=0))
        balance = int(result.get("balance", 0)) / 1e6
        print(f"  Live bankroll: ${balance:.2f} USDC")
        return max(balance, 1.0)
    except Exception as e:
        print(f"  Bankroll fetch error: {e} — using $20 fallback")
        return 20.0

def kelly_size(edge_pct, price, bankroll=20.0, frac=KELLY_FRAC):
    """
    Half-Kelly bet sizing.
    Kelly fraction = edge / (1 - price) for binary YES bets
    where edge = model_prob - market_price and price = market price paid.

    For a bet at price p with model prob q:
      Kelly % = (q - p) / (1 - p)   [for YES side]
    This gives fraction of bankroll to risk.
    """
    if price <= 0 or price >= 1:
        return MIN_BET
    # Net edge as fraction of potential profit
    b = (1.0 - price) / price   # odds: profit per $1 risked
    p = price + edge_pct        # model probability
    q = 1 - p                   # model probability of loss
    kelly_pct = (b * p - q) / b
    if kelly_pct <= 0:
        return MIN_BET
    bet = bankroll * frac * kelly_pct
    return round(max(MIN_BET, min(MAX_BET, bet)), 2)


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


def check_liquidity(token_id, price, min_depth=MIN_ASK_DEPTH):
    """Check if there's enough ask-side liquidity to fill our order."""
    try:
        import httpx
        r = httpx.get(f"https://clob.polymarket.com/order-book/{token_id}", timeout=6)
        if not r.is_success:
            return True  # can't check, allow through
        ob = r.json()
        asks = ob.get("asks", [])
        # Sum available $ within 20% of our price
        max_price = price * 1.2
        depth = sum(float(a["price"]) * float(a["size"]) for a in asks
                    if float(a["price"]) <= max_price)
        return depth >= min_depth
    except:
        return True  # fail open

def get_existing_exposure(creds, private_key):
    """
    Get current exposure (filled positions + open orders) grouped by city and date.
    Checks BOTH wallet positions (filled) and CLOB open orders (pending).
    Returns: {city: $amount}, {date: $amount}
    """
    import httpx, re

    city_exposure = {}
    date_exposure = {}

    def _add_exposure(question, cost):
        for city in ["Buenos Aires","Wellington","Seattle","NYC","New York","Miami",
                     "Chicago","Dallas","Atlanta","Toronto","London","Seoul","Paris"]:
            if city.lower() in question.lower():
                city_exposure[city] = city_exposure.get(city, 0) + cost
                break
        dm = re.search(r'(March|April|May) (\d+)', question)
        if dm:
            dt = f"{dm.group(1)} {dm.group(2)}"
            date_exposure[dt] = date_exposure.get(dt, 0) + cost

    # 1. Check FILLED positions from wallet (this is what was missing!)
    try:
        env = load_env()
        wallet = env.get("POLY_ADDRESS", "")
        r = httpx.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet, "sizeThreshold": "0.5"},
            timeout=10
        )
        if r.is_success:
            for pos in r.json():
                size      = float(pos.get("size", 0))
                avg_price = float(pos.get("avgPrice", 0) or 0)
                cost      = size * avg_price
                # fetch question from gamma API
                token_id = pos.get("asset") or pos.get("tokenId", "")
                end_date = pos.get("endDate", "")
                title    = pos.get("title", "") or pos.get("question", "")
                if title:
                    _add_exposure(title, cost)
    except Exception as e:
        print(f"  Wallet exposure check error: {e}")

    # 2. Check OPEN orders from CLOB (pending, not yet filled)
    try:
        import py_clob_client.http_helpers.helpers as h
        h._http_client = httpx.Client(http2=True, timeout=20, verify=False)
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OpenOrderParams
        api_creds = ApiCreds(api_key=creds["apiKey"], api_secret=creds["secret"], api_passphrase=creds["passphrase"])
        client = ClobClient("https://clob.polymarket.com", key=private_key, chain_id=137, creds=api_creds, signature_type=0)
        orders = client.get_orders(OpenOrderParams()) or []
        for o in orders:
            price = float(o.get("price", 0))
            size  = float(o.get("size_matched", 0))   # remaining unfilled size
            orig  = float(o.get("original_size", 0))
            remaining = orig - size
            cost  = price * remaining
            q     = o.get("question", "") or ""
            if q:
                _add_exposure(q, cost)
    except Exception as e:
        print(f"  Open order exposure check error: {e}")

    return city_exposure, date_exposure

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

    print("[AutoTrader] Polymarket Weather Arb — Half-Kelly Sizing")
    print(f"  Scan date : {results.get('date')}")
    print(f"  Min date  : {MIN_DATE}  (skipping today's resolved markets)")
    print(f"  Eligible  : {len(opps)} opportunities with >{MIN_EDGE:.0%} edge")
    print(f"  Trading   : top {MAX_TRADES}")
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

    # Fetch live bankroll + existing exposure
    bankroll = get_live_bankroll(private_key, creds)
    city_exposure, date_exposure = get_existing_exposure(creds, private_key)
    if city_exposure or date_exposure:
        print(f"  City exposure  : {dict((k, f'${v:.2f}') for k,v in city_exposure.items())}")
        print(f"  Date exposure  : {dict((k, f'${v:.2f}') for k,v in date_exposure.items())}")

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

        # ── Liquidity check ──────────────────────────────────────
        if not check_liquidity(token_id, price):
            print(f"  -> SKIP: insufficient order book depth (< ${MIN_ASK_DEPTH})")
            continue

        # ── Concentration limits ──────────────────────────────────
        import re
        city_match = next((c for c in ["Buenos Aires","Wellington","Seattle","New York","NYC",
                           "Miami","Chicago","Dallas","Atlanta","Toronto","London","Seoul","Paris"]
                           if c.lower() in question.lower()), None)
        date_match_m = re.search(r'(March|April|May) (\d+)', question)
        date_key = f"{date_match_m.group(1)} {date_match_m.group(2)}" if date_match_m else None

        if city_match and city_exposure.get(city_match, 0) >= MAX_CITY_EXPOSURE:
            print(f"  -> SKIP: already ${city_exposure[city_match]:.2f} deployed in {city_match} (limit ${MAX_CITY_EXPOSURE})")
            continue
        if date_key and date_exposure.get(date_key, 0) >= MAX_DATE_EXPOSURE:
            print(f"  -> SKIP: already ${date_exposure[date_key]:.2f} deployed on {date_key} (limit ${MAX_DATE_EXPOSURE})")
            continue

        # ── Kelly sizing — bet proportional to edge using live bankroll
        trade_dollars = kelly_size(abs(edge), price, bankroll=bankroll)
        raw_size = trade_dollars / price
        size = max(1.0, round(raw_size, 1))
        cost = price * size

        print(f"[{i}/{MAX_TRADES}] BUY {signal}  |  edge {edge:+.1%}  |  date {mdate}")
        print(f"  Q   : {question[:70]}")
        print(f"  Kelly bet: ${trade_dollars:.2f}  |  Price: ${price:.4f}  |  Shares: {size}  |  Cost: ~${cost:.2f}")
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

            trade_record = {
                "placed_at"    : datetime.utcnow().isoformat(),
                "question"     : question,
                "city"         : opp.get("city", ""),
                "market_date"  : mdate,
                "signal"       : signal,
                "token_id"     : token_id,
                "entry_price"  : price,
                "model_prob"   : opp.get("model_prob"),
                "forecast_mean": opp.get("forecast_mean"),
                "forecast_std" : opp.get("forecast_std"),
                "models_used"  : opp.get("models_used"),
                "edge_pct"     : edge,
                "size"         : size,
                "cost"         : cost,
                "order_id"     : order_id,
                "status"       : status,
                "error"        : error_msg,
                "resolved"     : False,
                "outcome"      : None,
                "final_price"  : None,
                "pnl"          : None,
            }
            log.append(trade_record)

            # Append to persistent trades DB for resolution tracking
            trades_db = BASE / "trades_db.jsonl"
            with open(trades_db, "a") as f:
                f.write(json.dumps(trade_record) + "\n")

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
