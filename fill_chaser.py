"""
fill_chaser.py â€” Adaptive fill chaser for open Polymarket orders.

For each open LIVE order:
  1. Check current fill %
  2. Fetch current best ask from orderbook
  3. If still >MIN_EDGE at current market price â†’ cancel + re-place at market price
  4. If edge is gone â†’ cancel and walk away
  5. If fully filled â†’ nothing to do

Run every 5-10 minutes while orders are open.
"""

import json, time, httpx
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent

# Minimum edge required to keep chasing (if market moves against us, stop)
MIN_CHASE_EDGE = 0.10   # 10% â€” below this, cancel and don't replace
MAX_PRICE_JUMP = 0.05   # max price increase per chase step (don't overpay in one jump)
MIN_FILL_PCT   = 0.95   # consider "done" at 95%+ filled

def load_env():
    env = {}
    for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def get_best_ask(client, token_id):
    """Get current best ask price for a token from the orderbook."""
    try:
        ob = client.get_order_book(token_id)
        # py-clob-client returns an OrderBookSummary object
        asks = ob.asks if hasattr(ob, 'asks') else ob.get("asks", [])
        if asks:
            return float(min(asks, key=lambda x: float(x.price if hasattr(x, 'price') else x["price"])).price
                         if hasattr(asks[0], 'price') else
                         min(float(x["price"]) for x in asks))
    except Exception as e:
        print(f"  Orderbook error: {e}")
    return None

def get_model_prob(token_id):
    """Look up original model probability from scan_results.json."""
    results_file = BASE / "scan_results.json"
    if not results_file.exists():
        return None
    results = json.loads(results_file.read_text())
    for opp in results.get("opportunities", []):
        if opp.get("token_id") == token_id:
            return opp.get("model_prob"), opp.get("signal")
    return None, None

def main():
    env = load_env()
    private_key = env.get("POLY_PRIVATE_KEY")
    creds_file  = BASE / "creds.json"

    if not creds_file.exists():
        print("ERROR: creds.json not found")
        return

    creds = json.loads(creds_file.read_text())

    import py_clob_client.http_helpers.helpers as http_helpers
    http_helpers._http_client = httpx.Client(http2=True, timeout=30, verify=False)

    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OpenOrderParams

    api_creds = ApiCreds(
        api_key=creds["apiKey"],
        api_secret=creds["secret"],
        api_passphrase=creds["passphrase"],
    )
    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key, chain_id=137, creds=api_creds, signature_type=0,
    )

    print(f"[FillChaser] {datetime.now().strftime('%H:%M:%S')} â€” checking open orders...")
    open_orders = client.get_orders(OpenOrderParams())

    if not open_orders:
        print("  No open orders.")
        return

    print(f"  Found {len(open_orders)} open order(s)")

    for order in open_orders:
        order_id  = order["id"]
        token_id  = order["asset_id"]
        side      = order["side"]           # BUY
        orig_size = float(order["original_size"])
        matched   = float(order.get("size_matched", 0))
        cur_price = float(order["price"])
        fill_pct  = matched / orig_size if orig_size else 0
        remaining = orig_size - matched

        print(f"\n  Order {order_id[:16]}...")
        print(f"  Fill: {matched:.1f}/{orig_size:.1f} ({fill_pct:.0%}) @ {cur_price:.4f}")

        # Already done
        if fill_pct >= MIN_FILL_PCT:
            print(f"  â†’ Fully filled ({fill_pct:.0%}), skipping")
            continue

        # Get model probability for this token
        model_prob, signal = get_model_prob(token_id)
        if model_prob is None:
            print(f"  â†’ No model data found, skipping")
            continue

        # Get current best ask
        best_ask = get_best_ask(client, token_id)
        if best_ask is None:
            print(f"  â†’ Could not fetch orderbook, skipping")
            continue

        print(f"  Best ask: {best_ask:.4f} | Model prob: {model_prob:.2%} | Signal: {signal}")

        # Calculate edge at current market price
        if signal == "YES":
            current_edge = model_prob - best_ask
            new_price = min(best_ask, cur_price + MAX_PRICE_JUMP)
        else:
            # BUY NO: token_id is the NO token, price is the NO price
            current_edge = (1 - model_prob) - best_ask
            new_price = min(best_ask, cur_price + MAX_PRICE_JUMP)

        print(f"  Edge at market: {current_edge:+.2%}")

        if current_edge < MIN_CHASE_EDGE:
            print(f"  â†’ Edge gone ({current_edge:.2%} < {MIN_CHASE_EDGE:.0%}), CANCELLING order")
            try:
                client.cancel(order_id)
                print(f"  â†’ Cancelled.")
            except Exception as e:
                print(f"  â†’ Cancel error: {e}")
            continue

        # Edge still good â€” if best ask is above our current price, chase it
        if best_ask > cur_price + 0.001:
            print(f"  â†’ Market moved up (ask {best_ask:.4f} > order {cur_price:.4f}), chasing...")
            new_price = round(min(best_ask, cur_price + MAX_PRICE_JUMP), 4)
            new_size  = round(remaining, 1)

            # Cancel old order
            try:
                client.cancel(order_id)
                print(f"  â†’ Cancelled old order")
                time.sleep(1)
            except Exception as e:
                print(f"  â†’ Cancel error: {e}")
                continue

            # Place new order at chased price
            try:
                order_args = OrderArgs(
                    token_id=token_id,
                    price=new_price,
                    size=new_size,
                    side="BUY",
                )
                signed = client.create_order(order_args)
                resp   = client.post_order(signed, orderType="GTC")
                new_id = resp.get("orderID", "") if isinstance(resp, dict) else ""
                status = resp.get("status", "") if isinstance(resp, dict) else str(resp)
                print(f"  â†’ New order @ {new_price:.4f} for {new_size} shares: {status} ({new_id[:20]})")
            except Exception as e:
                print(f"  â†’ Re-order error: {e}")
        else:
            print(f"  â†’ Order price ({cur_price:.4f}) is competitive, waiting for fills")

    print(f"\n[FillChaser] Done.")

if __name__ == "__main__":
    main()
