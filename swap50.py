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
wallet = Web3.to_checksum_address(env["POLY_ADDRESS"])
w3 = Web3(Web3.HTTPProvider("https://rpc-mainnet.matic.quiknode.pro"))
sys.stdout.write("Block: %d\n" % w3.eth.block_number)

USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
USDC_E      = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
ROUTER      = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")

ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]
ROUTER_ABI = [{"inputs":[{"components":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"fee","type":"uint24"},{"name":"recipient","type":"address"},{"name":"deadline","type":"uint256"},{"name":"amountIn","type":"uint256"},{"name":"amountOutMinimum","type":"uint256"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]

usdc_native = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
usdc_e      = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)
router      = w3.eth.contract(address=ROUTER, abi=ROUTER_ABI)

bal = usdc_native.functions.balanceOf(wallet).call()
sys.stdout.write("Native USDC: $%.4f\n" % (bal / 1e6))

AMOUNT = 50.0
amount_in = int(AMOUNT * 1e6)
if bal < amount_in:
    sys.stdout.write("ERROR: Not enough native USDC!\n")
    sys.exit(1)

# Approve if needed
allowance = usdc_native.functions.allowance(wallet, ROUTER).call()
if allowance < amount_in:
    sys.stdout.write("Approving router...\n")
    nonce = w3.eth.get_transaction_count(wallet)
    gp = w3.eth.gas_price
    tx = usdc_native.functions.approve(ROUTER, 2**256 - 1).build_transaction({
        "from": wallet, "nonce": nonce, "gas": 100000, "gasPrice": int(gp * 1.2), "chainId": 137
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    sys.stdout.write("Approve TX: %s\n" % txh.hex())
    w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    sys.stdout.write("Approved!\n")
    time.sleep(2)
else:
    sys.stdout.write("Already approved.\n")

# Swap
sys.stdout.write("Swapping $50 native USDC -> USDC.e...\n")
sys.stdout.flush()
nonce = w3.eth.get_transaction_count(wallet)
gp = w3.eth.gas_price
deadline = int(time.time()) + 300
params = (USDC_NATIVE, USDC_E, 100, wallet, deadline, amount_in, 0, 0)
tx = router.functions.exactInputSingle(params).build_transaction({
    "from": wallet, "nonce": nonce, "gas": 300000, "gasPrice": int(gp * 1.2), "chainId": 137
})
signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
txh = w3.eth.send_raw_transaction(signed.raw_transaction)
sys.stdout.write("Swap TX: %s\n" % txh.hex())
sys.stdout.write("Waiting for confirmation...\n")
sys.stdout.flush()
receipt = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
status = "OK" if receipt["status"] == 1 else "FAILED"
sys.stdout.write("Status: %s (block %d)\n" % (status, receipt["blockNumber"]))
if receipt["status"] == 1:
    new_bal = usdc_e.functions.balanceOf(wallet).call()
    sys.stdout.write("New USDC.e balance: $%.4f\n" % (new_bal / 1e6))
    sys.stdout.write("Done! Run approve_usdc.py if needed.\n")
else:
    sys.stdout.write("Swap failed. Try fee tier 500.\n")
