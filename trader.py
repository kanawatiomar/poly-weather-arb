"""
Polymarket Weather Arb - Trading Module
Handles: wallet setup, API auth, order placement, position tracking

Usage:
    python trader.py setup          # Generate wallet + API credentials
    python trader.py balance        # Check USDC balance
    python trader.py buy <token_id> <price> <size>    # Place limit order
    python trader.py positions      # List open positions
    python trader.py cancel <order_id>
"""

import os, sys, json
from pathlib import Path

DOTENV_PATH = Path(__file__).parent / ".env"
CREDS_PATH = Path(__file__).parent / "creds.json"
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


def load_env():
    """Load .env file."""
    env = {}
    if DOTENV_PATH.exists():
        for line in DOTENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def get_client():
    """Initialize the CLOB client with stored credentials."""
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    env = load_env()
    pk = env.get("POLY_PRIVATE_KEY")
    if not pk:
        print("ERROR: POLY_PRIVATE_KEY not set in .env")
        print("Run: python trader.py setup")
        sys.exit(1)

    creds = None
    if CREDS_PATH.exists():
        c = json.loads(CREDS_PATH.read_text())
        creds = ApiCreds(
            api_key=c["apiKey"],
            api_secret=c["secret"],
            api_passphrase=c["passphrase"],
        )

    client = ClobClient(HOST, chain_id=CHAIN_ID, key=pk, creds=creds)
    return client


def cmd_setup():
    """Generate wallet + API credentials."""
    from eth_account import Account

    env = load_env()

    if env.get("POLY_PRIVATE_KEY"):
        pk = env["POLY_PRIVATE_KEY"]
        acct = Account.from_key(pk)
        print(f"Existing wallet found: {acct.address}")
    else:
        # Generate new wallet
        acct = Account.create()
        pk = acct.key.hex()
        print(f"Generated new wallet: {acct.address}")
        print(f"Private key: {pk}")
        print()

        # Save to .env
        env_lines = []
        if DOTENV_PATH.exists():
            env_lines = DOTENV_PATH.read_text().splitlines()

        env_lines.append(f"POLY_PRIVATE_KEY={pk}")
        env_lines.append(f"POLY_ADDRESS={acct.address}")
        DOTENV_PATH.write_text("\n".join(env_lines))
        print(f"Saved to {DOTENV_PATH}")

    print()
    print("=" * 50)
    print("IMPORTANT: Fund this wallet before trading")
    print(f"Address: {acct.address}")
    print("Network: Polygon (MATIC)")
    print("Token: USDC (native Polygon USDC)")
    print()
    print("Steps:")
    print("1. Send USDC to this address on Polygon network")
    print("2. Also send a tiny bit of MATIC for gas (~$0.50 worth)")
    print("3. Then run: python trader.py auth")
    print("=" * 50)


def cmd_auth():
    """Generate API credentials from wallet."""
    from py_clob_client.client import ClobClient

    env = load_env()
    pk = env.get("POLY_PRIVATE_KEY")
    if not pk:
        print("ERROR: Run 'python trader.py setup' first")
        sys.exit(1)

    print("Generating API credentials...")
    client = ClobClient(HOST, chain_id=CHAIN_ID, key=pk)

    try:
        creds = client.create_or_derive_api_creds()
        print("Credentials generated:")
        print(f"  API Key: {creds.api_key}")
        print(f"  Secret: {creds.api_secret[:10]}...")
        print(f"  Passphrase: {creds.api_passphrase[:10]}...")

        # Save
        CREDS_PATH.write_text(json.dumps({
            "apiKey": creds.api_key,
            "secret": creds.api_secret,
            "passphrase": creds.api_passphrase,
        }, indent=2))
        print(f"\nSaved to {CREDS_PATH}")
    except Exception as e:
        print(f"ERROR: {e}")
        print("Make sure your wallet has MATIC for gas and is funded with USDC")


def cmd_balance():
    """Check USDC balance."""
    client = get_client()
    try:
        bal = client.get_balance_allowance()
        print(f"USDC Balance: ${bal}")
    except Exception as e:
        # Try alternate
        try:
            from eth_account import Account
            env = load_env()
            acct = Account.from_key(env["POLY_PRIVATE_KEY"])
            print(f"Wallet: {acct.address}")
            print(f"Error getting balance via CLOB: {e}")
            print("Check manually at: https://polygonscan.com/address/" + acct.address)
        except Exception as e2:
            print(f"Error: {e2}")


def cmd_buy(token_id, price, size):
    """
    Place a limit BUY order.
    token_id: CLOB token ID for the outcome (from market data)
    price: price per share (0.0 to 1.0)
    size: number of shares (= USDC amount since price*size = cost)
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, MarketOrderArgs
    from py_clob_client.constants import BUY

    client = get_client()
    price = float(price)
    size = float(size)

    print(f"Placing BUY order: {size} shares @ ${price:.4f} = ${price*size:.2f} USDC")
    print(f"Token ID: {token_id[:20]}...")

    try:
        # Create and sign order
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )
        signed_order = client.create_order(order_args)

        # Post to CLOB
        resp = client.post_order(signed_order, order_type="GTC")
        print(f"Order placed: {resp}")

        # Log it
        log_trade(token_id, "BUY", price, size, resp)

    except Exception as e:
        print(f"ERROR placing order: {e}")
        import traceback
        traceback.print_exc()


def cmd_sell(token_id, price, size):
    """Place a limit SELL order (to exit a position or buy NO)."""
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs
    from py_clob_client.constants import SELL

    client = get_client()
    price = float(price)
    size = float(size)

    print(f"Placing SELL order: {size} shares @ ${price:.4f}")

    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=SELL,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, order_type="GTC")
        print(f"Order placed: {resp}")
        log_trade(token_id, "SELL", price, size, resp)
    except Exception as e:
        print(f"ERROR: {e}")


def cmd_positions():
    """List current open orders and positions."""
    client = get_client()
    try:
        orders = client.get_orders()
        print(f"Open orders: {len(orders) if orders else 0}")
        for o in (orders or []):
            print(f"  {o}")
    except Exception as e:
        print(f"Error getting orders: {e}")

    try:
        positions = client.get_positions()
        print(f"\nPositions: {len(positions) if positions else 0}")
        for p in (positions or []):
            print(f"  {p}")
    except Exception as e:
        print(f"Error getting positions: {e}")


def cmd_cancel(order_id):
    """Cancel an open order."""
    client = get_client()
    try:
        resp = client.cancel(order_id)
        print(f"Cancelled: {resp}")
    except Exception as e:
        print(f"Error: {e}")


def log_trade(token_id, side, price, size, response):
    """Log a trade to trades.jsonl."""
    import time
    log_path = Path(__file__).parent / "trades.jsonl"
    entry = {
        "ts": int(time.time()),
        "token_id": token_id,
        "side": side,
        "price": price,
        "size": size,
        "cost": price * size,
        "response": str(response),
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Trade logged to {log_path}")


def cmd_get_token_id(market_id, outcome="Yes"):
    """Get the CLOB token ID for a market outcome."""
    import requests
    HEADERS = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", headers=HEADERS, timeout=10)
    if r.ok:
        m = r.json()
        clob_ids = json.loads(m.get("clobTokenIds", "[]"))
        outcomes = json.loads(m.get("outcomes", "[]"))
        print(f"Market: {m.get('question', '')[:80]}")
        for o, tid in zip(outcomes, clob_ids):
            print(f"  {o}: {tid}")
        return clob_ids, outcomes
    print(f"Market {market_id} not found")
    return [], []


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "help":
        print(__doc__)
    elif args[0] == "setup":
        cmd_setup()
    elif args[0] == "auth":
        cmd_auth()
    elif args[0] == "balance":
        cmd_balance()
    elif args[0] == "buy" and len(args) == 4:
        cmd_buy(args[1], args[2], args[3])
    elif args[0] == "sell" and len(args) == 4:
        cmd_sell(args[1], args[2], args[3])
    elif args[0] == "positions":
        cmd_positions()
    elif args[0] == "cancel" and len(args) == 2:
        cmd_cancel(args[1])
    elif args[0] == "token" and len(args) >= 2:
        outcome = args[2] if len(args) > 2 else "Yes"
        cmd_get_token_id(args[1], outcome)
    else:
        print(f"Unknown command: {args[0]}")
        print("Run: python trader.py help")
