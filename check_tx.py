import requests, json

addr = "0x85e8B5Ec6d45acF6C1E17bcfbD262442AD59E12B"

# Try multiple Polygon RPCs
RPCS = [
    "https://rpc-mainnet.matic.quiknode.pro",
    "https://rpc.ankr.com/polygon",
    "https://1rpc.io/matic",
]

def rpc_call(rpc_url, method, params):
    try:
        r = requests.post(rpc_url, json={"jsonrpc":"2.0","method":method,"params":params,"id":1}, timeout=8)
        return r.json().get("result")
    except Exception as e:
        return None

for rpc in RPCS:
    print(f"Trying {rpc}...")
    bal_hex = rpc_call(rpc, "eth_getBalance", [addr, "latest"])
    if bal_hex:
        matic = int(bal_hex, 16) / 1e18
        print(f"  POL balance: {matic:.6f}")

        addr_padded = addr[2:].lower().zfill(64)
        data = "0x70a08231" + addr_padded
        for name, contract in [
            ("USDC native", "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
            ("USDC.e",      "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
        ]:
            result = rpc_call(rpc, "eth_call", [{"to": contract, "data": data}, "latest"])
            if result and result != "0x":
                raw = int(result, 16)
                print(f"  {name}: ${raw / 1e6:.2f}")
        break
    else:
        print("  Failed")
