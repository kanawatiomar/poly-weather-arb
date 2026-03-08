import httpx
from pathlib import Path

env = {}
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

proxy = env.get("POLY_PROXY")
print(f"Proxy URL: {proxy}")

# Test actual IP through proxy
r = httpx.get("https://ipinfo.io/json", proxy=proxy, verify=False, timeout=15)
data = r.json()
print(f"IP via proxy : {data.get('ip')}")
print(f"Country      : {data.get('country')}")
print(f"City         : {data.get('city')}")
print(f"Org          : {data.get('org')}")

# Also test without proxy for comparison
r2 = httpx.get("https://ipinfo.io/json", timeout=10)
data2 = r2.json()
print(f"\nReal IP      : {data2.get('ip')}")
print(f"Real Country : {data2.get('country')}")
