"""Check USDC balance on both Polygon USDC contracts + POL balance"""
from web3 import Web3

env = {}
for line in open(".env").read().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

wallet = env["POLY_ADDRESS"]
w3 = Web3(Web3.HTTPProvider("https://rpc-mainnet.matic.quiknode.pro"))

ABI = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf",
        "outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
       {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
        "name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]

USDC_E  = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")  # USDC.e (bridged)
USDC_N  = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")  # Native USDC
CTF     = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_CTF = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")

pol_bal = w3.eth.get_balance(wallet)
print(f"Wallet : {wallet}")
print(f"POL    : {pol_bal / 1e18:.4f}")

for label, addr in [("USDC.e (bridged)", USDC_E), ("USDC native", USDC_N)]:
    c = w3.eth.contract(address=addr, abi=ABI)
    bal = c.functions.balanceOf(wallet).call()
    al1 = c.functions.allowance(wallet, CTF).call()
    al2 = c.functions.allowance(wallet, NEG_CTF).call()
    print(f"\n{label} ({addr})")
    print(f"  Balance          : ${bal/1e6:.4f}")
    print(f"  CTF allowance    : ${al1/1e6:.2f}")
    print(f"  NegRisk allowance: ${al2/1e6:.2f}")
