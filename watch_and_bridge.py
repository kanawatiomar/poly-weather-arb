"""
watch_and_bridge.py -- Watch for incoming native USDC and auto-bridge to USDC.e.
Polls every 20 seconds, runs swap_usdc.py as soon as balance arrives.
"""
import time, subprocess, sys
from pathlib import Path
from web3 import Web3

BASE = Path(__file__).parent
env = {}
for line in (BASE / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

w3 = Web3(Web3.HTTPProvider("https://rpc-mainnet.matic.quiknode.pro"))
USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
wallet = Web3.to_checksum_address(env["POLY_ADDRESS"])

ERC20_ABI = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf",
              "outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
contract = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)

py = sys.executable
print(f"Watching for native USDC on {wallet}...")

while True:
    bal = contract.functions.balanceOf(wallet).call() / 1e6
    print(f"  Native USDC: ${bal:.4f}", end="\r")
    if bal >= 1.0:
        print(f"\n  ${bal:.2f} USDC detected! Bridging now...")
        result = subprocess.run([py, str(BASE / "swap_usdc.py")], capture_output=True, text=True)
        print(result.stdout)
        if result.returncode == 0:
            print("Bridge complete!")
        else:
            print(f"Bridge error: {result.stderr}")
        break
    time.sleep(20)
