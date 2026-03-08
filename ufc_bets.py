import json, time, sys
from pathlib import Path
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds

BUY = 'BUY'
HOST = 'https://clob.polymarket.com'
CHAIN_ID = 137

def load_env():
    env = {}
    p = Path('.env')
    if p.exists():
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

TRADES = [
    ('Holloway wins Main Event 64.5pct', '70680997252637870845060297313707679888294694430159052880614680512560318175930', 0.645, 9),
    ('Rosas Jr wins 73.5pct', '101239751429668060313710372992353945289674063614750487143676549797501063649600', 0.735, 5),
    ('Borralho wins 66.5pct', '85359326900765264538832647041057004371172192978060984100891837719760639793294', 0.665, 3),
]

results = []
for desc, token_id, price, shares in TRADES:
    cost = price * shares
    sys.stdout.write('BET: %s | cost: $%.2f | payout: $%d\n' % (desc, cost, shares))
    sys.stdout.flush()
    try:
        order_args = OrderArgs(token_id=token_id, price=price, size=float(shares), side=BUY)
        signed = client.create_order(order_args)
        resp = client.post_order(signed, orderType='GTC')
        sys.stdout.write('  PLACED: %s\n' % str(resp))
        results.append({'desc': desc, 'status': 'ok', 'cost': cost, 'shares': shares, 'resp': str(resp)})
    except Exception as e:
        sys.stdout.write('  ERROR: %s\n' % str(e))
        results.append({'desc': desc, 'status': 'error', 'error': str(e)})
    sys.stdout.flush()
    time.sleep(1)

ok = sum(1 for r in results if r['status'] == 'ok')
total = sum(r.get('cost', 0) for r in results)
sys.stdout.write('\nPlaced %d/%d | Total: $%.2f\n' % (ok, len(results), total))
with open('ufc_bets.json', 'w') as f:
    json.dump({'ts': time.time(), 'bets': results}, f, indent=2)
