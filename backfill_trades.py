"""
backfill_trades.py — Backfills trades_db.jsonl from wallet positions.
Run once to seed the resolution tracker with existing positions.
Model prob will be estimated from scan_results.json where available.
"""

import json, httpx
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent
TRADES_DB = BASE / "trades_db.jsonl"

def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def get_price(token_id):
    try:
        r = httpx.get(f"https://clob.polymarket.com/last-trade-price?token_id={token_id}", timeout=6)
        p = float(r.json().get("price", 0))
        if p > 0: return p
    except: pass
    try:
        r = httpx.get(f"https://clob.polymarket.com/midpoint?token_id={token_id}", timeout=6)
        p = float(r.json().get("mid", 0))
        if p > 0: return p
    except: pass
    return None

def fetch_market_info(token_id):
    try:
        r = httpx.get(f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}", timeout=8)
        data = r.json()
        if isinstance(data, list) and data:
            return data[0].get("question",""), data[0].get("endDate","")[:10]
    except: pass
    return "", ""

def main():
    env    = load_env()
    wallet = env.get("POLY_ADDRESS","")

    # Load existing trades_db to avoid duplication
    existing_tokens = set()
    if TRADES_DB.exists():
        for line in TRADES_DB.read_text().splitlines():
            if line.strip():
                try:
                    t = json.loads(line)
                    existing_tokens.add(t.get("token_id",""))
                except: pass

    # Load scan_results for model data lookup
    model_lookup = {}
    scan_file = BASE / "scan_results.json"
    if scan_file.exists():
        try:
            scan = json.loads(scan_file.read_text())
            for opp in scan.get("opportunities", []):
                model_lookup[opp["token_id"]] = opp
        except: pass

    # Fetch wallet positions
    r = httpx.get(
        "https://data-api.polymarket.com/positions",
        params={"user": wallet, "sizeThreshold": "0.1"}, timeout=10
    )
    positions = r.json()
    print(f"Found {len(positions)} wallet positions")

    new_count = 0
    with open(TRADES_DB, "a") as f:
        for pos in positions:
            token_id  = pos.get("asset") or pos.get("tokenId","")
            size      = float(pos.get("size", 0))
            avg_price = float(pos.get("avgPrice", 0) or 0)
            outcome   = pos.get("outcome","Yes")
            title     = pos.get("title","") or pos.get("question","")
            end_date  = (pos.get("endDate","") or "")[:10]

            if not token_id or token_id in existing_tokens:
                continue

            cur_price = get_price(token_id)
            if not title:
                title, end_date = fetch_market_info(token_id)

            # Determine signal (YES or NO token)
            signal = "YES" if outcome.lower() == "yes" else "NO"

            # Check resolution
            resolved    = False
            market_outcome = None
            final_price = cur_price
            pnl         = None

            if cur_price is not None:
                if cur_price >= 0.97:
                    resolved = True
                    market_outcome = "WIN"
                    pnl = (cur_price - avg_price) * size
                elif cur_price <= 0.03:
                    resolved = True
                    market_outcome = "LOSS"
                    pnl = (cur_price - avg_price) * size

            # Try to get model data from scan_results
            scan_opp = model_lookup.get(token_id, {})

            record = {
                "placed_at"    : "backfilled",
                "question"     : title,
                "city"         : scan_opp.get("city", ""),
                "market_date"  : end_date,
                "signal"       : signal,
                "token_id"     : token_id,
                "entry_price"  : avg_price,
                "model_prob"   : scan_opp.get("model_prob"),
                "forecast_mean": scan_opp.get("forecast_mean"),
                "forecast_std" : scan_opp.get("forecast_std"),
                "models_used"  : scan_opp.get("models_used"),
                "edge_pct"     : scan_opp.get("edge_pct"),
                "size"         : size,
                "cost"         : round(avg_price * size, 4),
                "order_id"     : "",
                "status"       : "backfilled",
                "error"        : "",
                "resolved"     : resolved,
                "outcome"      : market_outcome,
                "final_price"  : final_price,
                "pnl"          : round(pnl, 4) if pnl is not None else None,
                "resolved_at"  : datetime.utcnow().isoformat() if resolved else None,
            }

            f.write(json.dumps(record) + "\n")
            existing_tokens.add(token_id)
            new_count += 1

            status_str = f"{market_outcome} P&L={pnl:+.2f}" if resolved else f"open @ {cur_price:.3f}"
            print(f"  [{signal}] {title[:55]} | entry={avg_price:.3f} | {status_str}")

    print(f"\nBackfilled {new_count} positions to trades_db.jsonl")

if __name__ == "__main__":
    main()
