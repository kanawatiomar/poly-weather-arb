"""
Tests whether the httpx patch actually routes CLOB requests through the proxy.
"""
import httpx
from pathlib import Path
import py_clob_client.http_helpers.helpers as http_helpers

env = {}
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

proxy_url = env["POLY_PROXY"]
print(f"Proxy: {proxy_url}")

# Patch
patched_client = httpx.Client(http2=True, proxy=proxy_url, verify=False, timeout=30)
http_helpers._http_client = patched_client
print(f"Patched: {http_helpers._http_client}")

# Try a public CLOB endpoint (no auth needed)
from py_clob_client.http_helpers.helpers import get
result = get("https://clob.polymarket.com/")
print(f"CLOB root response: {str(result)[:200]}")
