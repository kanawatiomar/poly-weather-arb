"""
approve_usdc.py — One-time on-chain USDC approval for Polymarket CLOB contracts.
Run this ONCE before trading. Takes ~30 seconds.
"""
import json, sys, time
from pathlib import Path
from web3 import Web3

BASE = Path(__file__).parent

# --- Load .env ---
env = {}
for line in (BASE / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

private_key = env["POLY_PRIVATE_KEY"]
wallet_addr = env["POLY_ADDRESS"]

# --- Polygon RPC ---
RPC_URL = "https://rpc-mainnet.matic.quiknode.pro"
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    print("ERROR: Cannot connect to Polygon RPC")
    sys.exit(1)

print(f"Connected to Polygon. Block: {w3.eth.block_number}")

# --- Contract Addresses (Polymarket on Polygon) ---
USDC_ADDR      = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE   = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_ADDR  = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")

MAX_UINT256 = 2**256 - 1

ERC20_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "owner",   "type": "address"},
            {"name": "spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
]

usdc = w3.eth.contract(address=USDC_ADDR, abi=ERC20_ABI)
acct = wallet_addr

# --- Check current balance ---
balance = usdc.functions.balanceOf(acct).call()
print(f"USDC balance: ${balance / 1e6:.4f}")

# --- Check existing allowances ---
allow_ctf      = usdc.functions.allowance(acct, CTF_EXCHANGE).call()
allow_neg_risk = usdc.functions.allowance(acct, NEG_RISK_ADDR).call()
print(f"CTF Exchange allowance    : ${allow_ctf / 1e6:.2f}")
print(f"Neg Risk Exchange allowance: ${allow_neg_risk / 1e6:.2f}")

# --- Approve both contracts if needed ---
THRESHOLD = 1_000_000 * 1e6  # $1M — if below this, re-approve

def approve(spender_addr, label):
    print(f"\nApproving {label} ({spender_addr})...")
    nonce = w3.eth.get_transaction_count(acct)
    gas_price = w3.eth.gas_price
    tx = usdc.functions.approve(spender_addr, MAX_UINT256).build_transaction({
        "from"     : acct,
        "nonce"    : nonce,
        "gas"      : 100_000,
        "gasPrice" : int(gas_price * 1.2),
        "chainId"  : 137,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX sent: {tx_hash.hex()}")
    print(f"  Waiting for confirmation...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"  Confirmed in block {receipt['blockNumber']} — status: {'OK' if receipt['status'] == 1 else 'FAILED'}")
    return receipt["status"] == 1

if allow_ctf < THRESHOLD:
    approve(CTF_EXCHANGE, "CTF Exchange")
else:
    print(f"\nCTF Exchange already approved (${allow_ctf/1e6:.0f})")

if allow_neg_risk < THRESHOLD:
    approve(NEG_RISK_ADDR, "Neg Risk CTF Exchange")
else:
    print(f"Neg Risk already approved (${allow_neg_risk/1e6:.0f})")

print("\nDone! Now run: python auto_trade.py")
