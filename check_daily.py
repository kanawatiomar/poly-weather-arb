"""Quick check on daily temperature markets."""
import requests, json
from datetime import date

HEADERS = {"User-Agent": "Mozilla/5.0"}

cities = ["nyc", "london", "seoul", "miami", "chicago", "seattle", "dallas", "atlanta", "wellington", "toronto", "paris"]
today = date(2026, 3, 6)

for city in cities:
    month_name = today.strftime("%B").lower()
    slug = f"highest-temperature-in-{city}-on-{month_name}-{today.day}-{today.year}"
    r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", headers=HEADERS, timeout=10)
    if r.ok and r.json():
        event = r.json()[0]
        markets = event.get("markets", [])
        total_vol = sum(float(m.get("volume") or 0) for m in markets)
        print(f"\n{event['title']} (${total_vol:.0f} vol)")
        for m in markets:
            if m.get("closed"):
                continue
            prices = json.loads(m.get("outcomePrices", "[]"))
            outcomes = json.loads(m.get("outcomes", "[]"))
            if "Yes" in outcomes:
                yes_price = float(prices[outcomes.index("Yes")])
                q = m.get("question", "")[:75]
                print(f"  {q:<75} {yes_price:.1%}")
    else:
        print(f"  {slug}: not found")
