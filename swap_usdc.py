"""
swap_usdc.py — Swap native USDC -> USDC.e on Polygon via Uniswap V3.
Polymarket uses USDC.e as collateral; wallet has native USDC.
Run this once to fund the trading account.
"""
import sys, time
from web3 import Web3
from pathlib import Path

BASE = Path(__file__).parent
env = {}
for line in (BASE / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

private_key = env["POLY_PRIVATE_KEY"]
wallet      = Web3.to_checksum_address(env["POLY_ADDRESS"])

w3 = Web3(Web3.HTTPProvider("https://rpc-mainnet.matic.quiknode.pro"))
print(f"Connected — block {w3.eth.block_number}")

USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
USDC_E      = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
ROUTER      = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")  # Uniswap V3 SwapRouter

ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf",
     "outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
     "name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]

ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn",           "type": "address"},
                {"name": "tokenOut",          "type": "address"},
                {"name": "fee",               "type": "uint24"},
                {"name": "recipient",         "type": "address"},
                {"name": "deadline",          "type": "uint256"},
                {"name": "amountIn",          "type": "uint256"},
                {"name": "amountOutMinimum",  "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "params",
            "type": "tuple"
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

usdc_native = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
usdc_e      = w3.eth.contract(address=USDC_E,      abi=ERC20_ABI)
router      = w3.eth.contract(address=ROUTER,       abi=ROUTER_ABI)

# --- Check balances ---
bal_native = usdc_native.functions.balanceOf(wallet).call()
bal_e      = usdc_e.functions.balanceOf(wallet).call()
print(f"\nNative USDC : ${bal_native/1e6:.4f}")
print(f"USDC.e      : ${bal_e/1e6:.4f}")

# --- How much to swap ---
SWAP_AMOUNT_USD = 25.0   # swap $25 native USDC -> USDC.e
amount_in = int(SWAP_AMOUNT_USD * 1e6)

if bal_native < amount_in:
    print(f"\nERROR: Only ${bal_native/1e6:.2f} native USDC available, need ${SWAP_AMOUNT_USD}")
    sys.exit(1)

print(f"\nSwapping ${SWAP_AMOUNT_USD} native USDC -> USDC.e ...")

# --- Step 1: Approve router to spend native USDC ---
allowance = usdc_native.functions.allowance(wallet, ROUTER).call()
if allowance < amount_in:
    print("Approving router...")
    nonce = w3.eth.get_transaction_count(wallet)
    gas_price = w3.eth.gas_price
    tx = usdc_native.functions.approve(ROUTER, 2**256 - 1).build_transaction({
        "from": wallet, "nonce": nonce,
        "gas": 100_000, "gasPrice": int(gas_price * 1.2), "chainId": 137,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  Approve TX: {txh.hex()}")
    w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    print("  Approved!")
    time.sleep(2)

# --- Step 2: Swap ---
nonce     = w3.eth.get_transaction_count(wallet)
gas_price = w3.eth.gas_price
deadline  = int(time.time()) + 300  # 5 min

# Fee tier 100 = 0.01% (stablecoin pool)
params = (USDC_NATIVE, USDC_E, 100, wallet, deadline, amount_in, 0, 0)

tx = router.functions.exactInputSingle(params).build_transaction({
    "from": wallet, "nonce": nonce,
    "gas": 300_000, "gasPrice": int(gas_price * 1.2), "chainId": 137,
})
signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
txh = w3.eth.send_raw_transaction(signed.raw_transaction)
print(f"Swap TX: {txh.hex()}")
print("Waiting for confirmation...")
receipt = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
print(f"Status: {'OK' if receipt['status'] == 1 else 'FAILED'} (block {receipt['blockNumber']})")

if receipt["status"] == 1:
    bal_e_after = usdc_e.functions.balanceOf(wallet).call()
    print(f"\nNew USDC.e balance: ${bal_e_after/1e6:.4f}")
    print("Now run: python approve_usdc.py  (to approve USDC.e for Neg Risk contract)")
    print("Then run: python auto_trade.py")
else:
    print("Swap failed! Try fee tier 500 (0.05%) instead of 100.")
