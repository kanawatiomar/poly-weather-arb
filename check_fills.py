import json, httpx
from pathlib import Path

env = {}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

import py_clob_client.http_helpers.helpers as h
h._http_client = httpx.Client(http2=True, timeout=30, verify=False)
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

creds = json.loads(Path('creds.json').read_text())
client = ClobClient('https://clob.polymarket.com', key=env['POLY_PRIVATE_KEY'], chain_id=137,
    creds=ApiCreds(api_key=creds['apiKey'], api_secret=creds['secret'], api_passphrase=creds['passphrase']),
    signature_type=0)

orders = json.loads(Path('C:/Users/kanaw/.openclaw/workspace/polymarket-dashboard/our_orders.json').read_text())

print(f"{'Label':<38} {'Status':<12} {'Filled':<20} {'Pct':>5}")
print("-" * 80)
for o in orders:
    if o.get('category', 'weather') != 'weather':
        continue
    try:
        r = client.get_order(o['order_id'])
        size    = float(r.get('original_size', 0))
        matched = float(r.get('size_matched', 0))
        pct     = matched / size * 100 if size else 0
        status  = r.get('status', '?')
        print(f"{o['label'][:38]:<38} {status:<12} {matched:.1f}/{size:.1f} shares  {pct:>4.0f}%")
    except Exception as e:
        print(f"{o['label'][:38]:<38} ERROR: {e}")
