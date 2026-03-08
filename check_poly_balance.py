"""Check what Polymarket's CLOB thinks our balance/allowance is"""
import json, httpx
from pathlib import Path
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
import py_clob_client.http_helpers.helpers as http_helpers

BASE = Path(__file__).parent
env = {}
for line in (BASE / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

creds = json.loads((BASE / "creds.json").read_text())
private_key = env["POLY_PRIVATE_KEY"]

http_helpers._http_client = httpx.Client(http2=True, timeout=30, verify=False)

from py_clob_client.clob_types import ApiCreds
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

# Ask Polymarket to sync on-chain balance to their DB
print("Calling update_balance_allowance (USDC collateral)...")
try:
    result = client.update_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=0)
    )
    print(f"  Result: {result}")
except Exception as e:
    print(f"  Error: {e}")

print("\nGetting balance_allowance...")
try:
    result = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=0)
    )
    print(f"  Result: {result}")
except Exception as e:
    print(f"  Error: {e}")
