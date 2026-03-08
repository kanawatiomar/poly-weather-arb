"""Approve the missing 3rd Polymarket contract + check market neg_risk status"""
import json, sys, time, httpx
from web3 import Web3
from pathlib import Path
import py_clob_client.http_helpers.helpers as http_helpers
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

BASE = Path(__file__).parent
env = {}
for line in (BASE / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

private_key = env["POLY_PRIVATE_KEY"]
wallet = Web3.to_checksum_address(env["POLY_ADDRESS"])
creds = json.loads((BASE / "creds.json").read_text())

# --- Approve 3rd contract on-chain ---
USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
THIRD  = Web3.to_checksum_address("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296")  # Neg Risk Adapter
MAX    = 2**256 - 1

ERC20_ABI = [
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
     "name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]

w3 = Web3(Web3.HTTPProvider("https://rpc-mainnet.matic.quiknode.pro"))
usdc_e = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

cur = usdc_e.functions.allowance(wallet, THIRD).call()
print(f"Current allowance for 3rd contract: ${cur/1e6:.2f}")

if cur < 1_000_000 * 1e6:
    print("Approving 3rd contract...")
    nonce = w3.eth.get_transaction_count(wallet)
    gas_price = w3.eth.gas_price
    tx = usdc_e.functions.approve(THIRD, MAX).build_transaction({
        "from": wallet, "nonce": nonce,
        "gas": 100_000, "gasPrice": int(gas_price * 1.2), "chainId": 137,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX: {txh.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    print(f"  Status: {'OK' if receipt['status']==1 else 'FAILED'}")
else:
    print("Already approved.")

# --- Check neg_risk status for top markets ---
print("\nChecking neg_risk status on top market tokens...")
http_helpers._http_client = httpx.Client(http2=True, timeout=30, verify=False)

api_creds = ApiCreds(
    api_key=creds["apiKey"],
    api_secret=creds["secret"],
    api_passphrase=creds["passphrase"],
)
client = ClobClient(
    "https://clob.polymarket.com",
    key=private_key, chain_id=137, creds=api_creds, signature_type=0,
)

# Top token IDs from scan
tokens = [
    ("Toronto NO",       "92779591599984128382137077564150071340567282117853030157887422197097013144677"),
    ("Seattle YES",      "102567352026290085856628839104621665686409288517421932202708161660473204828313"),
    ("Buenos Aires YES", "57943352243575948576143629791182850731864186713081832868447637831822276184969"),
]

for label, token_id in tokens:
    try:
        neg = client.get_neg_risk(token_id=token_id)
        print(f"  {label}: neg_risk={neg}")
    except Exception as e:
        print(f"  {label}: ERROR {e}")
