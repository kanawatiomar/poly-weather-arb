"""
model_stop.py -- Model-based exit system for Polymarket weather positions.

Unlike a price-based stop-loss, this re-runs the weather model on each
open position and only exits if the model's edge has FLIPPED against us.

Logic per position:
  - Re-fetch ensemble forecast for city + date
  - Re-calculate model probability for this market's temp range
  - Compute current edge = model_prob - current_market_price (for YES positions)
                        or (1 - model_prob) - current_market_price (for NO positions)
  - If edge < -FLIP_THRESHOLD: model now disagrees with our position --> SELL
  - If edge is still positive: hold (model still agrees)
  - If edge shrank but still positive: log warning, no action

Run every 1-2 hours or after major weather model updates (typically 06:00 and 18:00 UTC).
"""

import json, httpx, sys, time
from pathlib import Path
from datetime import date, datetime

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

# Import scanner utilities
from scanner import (
    detect_city, get_temperature_forecast, parse_temp_market,
    probability_in_range, CITIES
)

FLIP_THRESHOLD      = 0.10   # exit if model edge flipped by more than 10% against us
PROFIT_LOCK_EDGE    = 0.15   # lock profits when in-the-green AND edge compresses below 15%
PROFIT_LOCK_MIN_UP  = 1.5    # only lock profits if position is up 50%+ from entry
MIN_SIZE_TO_EXIT    = 1.0    # don't bother selling dust positions below this
DISCORD_CHANNEL     = "1479364504943857684"

def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def get_client(env, creds):
    import py_clob_client.http_helpers.helpers as h
    h._http_client = httpx.Client(http2=True, timeout=30, verify=False)
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    return ClobClient(
        "https://clob.polymarket.com",
        key=env["POLY_PRIVATE_KEY"], chain_id=137, signature_type=0,
        creds=ApiCreds(api_key=creds["apiKey"], api_secret=creds["secret"], api_passphrase=creds["passphrase"]),
    )

def get_current_price(token_id):
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

def get_best_bid(token_id):
    """Get best bid so we can sell into it."""
    try:
        r = httpx.get(f"https://clob.polymarket.com/order-book/{token_id}", timeout=6)
        if r.is_success:
            bids = r.json().get("bids", [])
            if bids:
                return float(max(bids, key=lambda x: float(x["price"]))["price"])
    except: pass
    return None

def post_discord(msg, token):
    if not token: return
    try:
        httpx.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"content": msg}, timeout=8,
        )
    except: pass

def sell_position(client, token_id, size, bid_price):
    """Place a SELL order at the current best bid."""
    from py_clob_client.clob_types import OrderArgs
    try:
        sell_price = round(max(bid_price * 0.98, 0.01), 4)  # slightly below bid to ensure fill
        order_args = OrderArgs(token_id=token_id, price=sell_price, size=round(size, 1), side="SELL")
        signed = client.create_order(order_args)
        resp   = client.post_order(signed, orderType="GTC")
        return resp
    except Exception as e:
        return {"error": str(e)}

def fetch_wallet_positions(wallet):
    try:
        r = httpx.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet, "sizeThreshold": "0.5"}, timeout=10
        )
        if r.is_success:
            return r.json()
    except: pass
    return []

def fetch_market_question(token_id):
    try:
        r = httpx.get(f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}", timeout=8)
        data = r.json()
        if isinstance(data, list) and data:
            return data[0].get("question",""), data[0].get("endDate","")[:10]
    except: pass
    return "", ""

def main():
    env   = load_env()
    creds = json.loads((BASE / "creds.json").read_text())
    discord_token = env.get("DISCORD_BOT_TOKEN","")

    client = get_client(env, creds)
    wallet = env.get("POLY_ADDRESS","")

    print(f"[ModelStop] {datetime.now().strftime('%H:%M:%S')} -- scanning positions...")

    positions = fetch_wallet_positions(wallet)
    print(f"  {len(positions)} wallet positions")

    import re
    exits = []
    holds = []
    adds  = []

    for pos in positions:
        token_id  = pos.get("asset") or pos.get("tokenId","")
        size      = float(pos.get("size", 0))
        avg_price = float(pos.get("avgPrice", 0) or pos.get("averagePrice", 0) or 0)
        outcome   = pos.get("outcome","Yes")   # "Yes" or "No"

        if size < MIN_SIZE_TO_EXIT or not token_id:
            continue

        question, end_date_str = fetch_market_question(token_id)

        # Only handle weather (temperature) markets
        weather_kw = ["temperature","celsius","fahrenheit","highest temp","lowest temp"]
        if not any(k in question.lower() for k in weather_kw):
            continue

        # Parse city + date
        city_key    = detect_city(question)
        if not city_key:
            continue

        # Parse target date
        dm = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})',question)
        if not dm:
            continue
        months = ['January','February','March','April','May','June',
                  'July','August','September','october','november','december']
        months = [m.capitalize() for m in ['january','february','march','april','may','june',
                  'july','august','september','october','november','december']]
        try:
            mo  = months.index(dm.group(1)) + 1
            day = int(dm.group(2))
            target_date = date(date.today().year, mo, day)
        except:
            continue

        # Skip already-resolved markets
        if target_date < date.today():
            continue

        # Parse temperature range
        parsed = parse_temp_market(question)
        if not parsed:
            continue

        # Re-run ensemble model
        mean_temp, std_temp = get_temperature_forecast(city_key, target_date)
        if mean_temp is None:
            print(f"  [{city_key}] Could not fetch forecast, skipping")
            continue

        # Model probability for YES outcome
        model_prob_yes = probability_in_range(mean_temp, std_temp, parsed["low"], parsed["high"])
        cur_price      = get_current_price(token_id)
        if cur_price is None:
            continue

        # Edge from our position's perspective
        if outcome == "Yes":
            our_edge = model_prob_yes - cur_price
        else:
            our_edge = (1 - model_prob_yes) - cur_price

        unit_sym   = "F" if CITIES[city_key]["unit"] == "fahrenheit" else "C"
        days_ahead = (target_date - date.today()).days

        print(f"\n  {question[:60]}")
        print(f"  City: {city_key} | Date: {target_date} ({days_ahead}d) | {outcome} pos @ {avg_price:.3f}")
        print(f"  Model: {mean_temp:.1f}+/-{std_temp:.1f}{unit_sym} | Model YES prob: {model_prob_yes:.2%}")
        print(f"  Current price: {cur_price:.3f} | Our edge: {our_edge:+.2%}")

        cost         = avg_price * size
        current_val  = cur_price * size
        unrealized   = current_val - cost

        if our_edge < -FLIP_THRESHOLD:
            # Model has flipped against us — EXIT
            bid = get_best_bid(token_id)
            msg = (
                f"🔄 **MODEL STOP** | {question[:60]}\n"
                f"Entry {avg_price:.3f} ({outcome}) | Now {cur_price:.3f} | "
                f"Model edge flipped to {our_edge:+.2%}\n"
                f"Forecast: {mean_temp:.1f}{unit_sym} | Unrealized: {'+'if unrealized>=0 else ''}"
                f"${unrealized:.2f} | **SELLING {size:.1f} shares**"
            )
            print(f"  !! EDGE FLIPPED ({our_edge:+.2%}) -- SELLING")

            if bid:
                resp = sell_position(client, token_id, size, bid)
                print(f"  Sell resp: {resp}")
                msg += f"\n  Sell @ {bid:.3f}"
            else:
                print(f"  No bid found, cannot sell")
                msg += "\n  ⚠️ No bid available to sell into"

            post_discord(msg, discord_token)
            exits.append(question[:50])

        elif (0 < our_edge < PROFIT_LOCK_EDGE
              and avg_price > 0
              and cur_price >= avg_price * PROFIT_LOCK_MIN_UP):
            # Market caught up to model — edge compressed while we're in profit → LOCK GAINS
            bid = get_best_bid(token_id)
            print(f"  $$ EDGE COMPRESSED ({our_edge:+.2%}), position up {cur_price/avg_price:.1f}x -- LOCKING PROFITS")
            msg = (
                f"💰 **PROFIT LOCK** | {question[:60]}\n"
                f"Entry {avg_price:.3f} → Now {cur_price:.3f} ({cur_price/avg_price:.1f}x) | "
                f"Model edge compressed to {our_edge:+.2%} — market caught up\n"
                f"Forecast: {mean_temp:.1f}{unit_sym} | Unrealized: **+${unrealized:.2f}** | "
                f"**SELLING {size:.1f} shares to lock gains**"
            )

            if bid:
                resp = sell_position(client, token_id, size, bid)
                print(f"  Sell resp: {resp}")
                msg += f"\n  Sell @ {bid:.3f}"
            else:
                print(f"  No bid found, cannot sell")
                msg += "\n  ⚠️ No bid — try manual sell"

            post_discord(msg, discord_token)
            exits.append(question[:50])

        elif our_edge < 0.05:
            # Edge mostly gone but not flipped — warn
            print(f"  >> Edge weak ({our_edge:+.2%}), monitoring")
            holds.append((question[:50], our_edge))

        elif our_edge > 0.30 and cur_price < avg_price * 0.5:
            # Price dropped but model still strong — potential add opportunity
            print(f"  ++ Model still strong ({our_edge:+.2%}), price dipped to {cur_price:.3f}")
            adds.append((question[:50], our_edge, cur_price))
            msg = (
                f"💡 **ADD OPPORTUNITY** | {question[:60]}\n"
                f"Entry {avg_price:.3f} | Now {cur_price:.3f} | Model edge still **{our_edge:+.2%}**\n"
                f"Forecast unchanged: {mean_temp:.1f}{unit_sym} | Market may be wrong — consider adding"
            )
            post_discord(msg, discord_token)

        else:
            print(f"  OK holding ({our_edge:+.2%} edge)")

        time.sleep(0.3)  # be nice to APIs

    print(f"\n[ModelStop] Done -- {len(exits)} exits | {len(holds)} weak | {len(adds)} add opportunities")

if __name__ == "__main__":
    main()
