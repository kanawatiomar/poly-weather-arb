import json

with open("scan_results.json") as f:
    data = json.load(f)

# Show all March 7 opportunities sorted by edge
opps = [o for o in data["opportunities"] if "march 7" in o["question"].lower() or "March 7" in o["question"]]
opps += [o for o in data["opportunities"] if "march" not in o["question"].lower() and "precipitation" in o["question"].lower()]
opps.sort(key=lambda x: abs(x["edge_pct"]), reverse=True)

print("AVAILABLE TRADES (token_id confirmed)")
print("=" * 90)
for o in opps[:15]:
    cost = o["yes_price"] if o["signal"] == "YES" else 1 - o["yes_price"]
    shares_per_5 = round(5 / cost, 1)
    print(f"{o['signal']:4} edge {o['edge_pct']:+.1%} | cost ${cost:.3f} | {shares_per_5} shares per $5")
    print(f"     {o['question'][:70]}")
    print(f"     token: {o['token_id']}")
    print()
