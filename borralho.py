import json, sys
from pathlib import Path
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds

BUY = 'BUY'
HOST = 'https://clob.polymarket.com'
CHAIN_ID = 137

def load_env():
    env = {}
    p = Path('.env')
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"')
    return env

env = load_env()
c = json.loads(Path('creds.json').read_text())
creds = ApiCreds(api_key=c['apiKey'], api_secret=c['secret'], api_passphrase=c['passphrase'])
proxy_url = env.get('POLY_PROXY')
if proxy_url:
    import httpx
    import py_clob_client.http_helpers.helpers as http_helpers
    try:
        http_helpers._http_client = httpx.Client(http2=True, proxy=proxy_url)
    except TypeError:
        http_helpers._http_client = httpx.Client(http2=True, proxies=proxy_url)

client = ClobClient(HOST, chain_id=CHAIN_ID, key=env['POLY_PRIVATE_KEY'], creds=creds)

token = '85359326900765264538832647041057004371172192978060984100891837719760639793294'
price = 0.665
shares = 5
cost = price * shares
sys.stdout.write('Borralho 5 shares | cost: $%.2f | payout: $5\n' % cost)
order_args = OrderArgs(token_id=token, price=price, size=float(shares), side=BUY)
signed = client.create_order(order_args)
resp = client.post_order(signed, orderType='GTC')
sys.stdout.write('PLACED: %s\n' % str(resp))
