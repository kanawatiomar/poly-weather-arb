import json

with open("scan_results.json") as f:
    data = json.load(f)

opps = data["opportunities"]

# Focus on range markets (between X-Y) — cleaner resolution criteria
range_opps = [
    o for o in opps
    if "between" in o["question"].lower() and abs(o["edge_pct"]) >= 0.12
]
range_opps.sort(key=lambda x: abs(x["edge_pct"]), reverse=True)

print("TOP RANGE-MARKET PLAYS")
print("=" * 80)
for o in range_opps[:12]:
    cost = o["yes_price"] if o["signal"] == "YES" else 1 - o["yes_price"]
    payout_per_dollar = (1 / cost) - 1
    print(f"{o['signal']:4} | edge {o['edge_pct']:+.1%} | cost ${cost:.2f}/share | payout {payout_per_dollar:.1f}x")
    print(f"     {o['question'][:70]}")
    print(f"     Market: {o['yes_price']:.1%} | Model: {o['model_prob']:.1%} | Vol: ${o['volume']:.0f}")
    print(f"     token_id: {o['token_id']}")
    print()
