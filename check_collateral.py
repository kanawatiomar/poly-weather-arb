"""Check what collateral token Polymarket CLOB actually uses"""
import json, httpx
from pathlib import Path

env = {}
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

creds = json.loads(Path("creds.json").read_text())
private_key = env["POLY_PRIVATE_KEY"]

import py_clob_client.http_helpers.helpers as http_helpers
http_helpers._http_client = httpx.Client(http2=True, timeout=30, verify=False)

from py_clob_client.client import ClobClient
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

print("Collateral (USDC) address:", client.get_collateral_address())
print("CTF Exchange address     :", client.get_exchange_address())
print("Conditional address      :", client.get_conditional_address())
