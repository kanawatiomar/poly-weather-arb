import json

with open("scan_results.json") as f:
    d = json.load(f)

mar8 = [o for o in d["opportunities"] if o.get("date", "") >= "2026-03-08"]
print(f"March 8+ opportunities: {len(mar8)}")
for o in sorted(mar8, key=lambda x: abs(x["edge_pct"]), reverse=True)[:10]:
    print(f"{o['signal']:<4} {o['edge_pct']:>+.1%}  {o['date']}  {o['question'][:65]}")
    print(f"     token: {o['token_id']}")
    print()
