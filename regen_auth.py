# -*- coding: utf-8 -*-
"""Regenerate API credentials through proxy so Polymarket sees German IP."""
import json, httpx
from pathlib import Path
from py_clob_client.client import ClobClient
import py_clob_client.http_helpers.helpers as hh

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

def load_env():
    env = {}
    for line in (Path(__file__).parent / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env

env = load_env()
proxy = env.get("POLY_PROXY")

print(f"Patching httpx through proxy: {proxy.split('@')[1] if proxy and '@' in proxy else proxy}")
hh._http_client = httpx.Client(http2=True, proxy=proxy)

# Verify
r = hh._http_client.get("https://ipv4.webshare.io/")
print(f"Current IP seen by Polymarket: {r.text.strip()}")

print("\nRegenerating API credentials...")
client = ClobClient(HOST, chain_id=CHAIN_ID, key=env["POLY_PRIVATE_KEY"])
creds = client.create_or_derive_api_creds()

print(f"New API Key: {creds.api_key}")
creds_path = Path(__file__).parent / "creds.json"
with open(creds_path, "w") as f:
    json.dump({
        "apiKey": creds.api_key,
        "secret": creds.api_secret,
        "passphrase": creds.api_passphrase,
    }, f, indent=2)
print(f"Saved to {creds_path}")
print("Now run: python trade_now.py")
