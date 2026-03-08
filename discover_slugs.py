"""Paginate all Polymarket events to find all weather/precipitation/temperature slugs."""
import requests, json

HEADERS = {"User-Agent": "Mozilla/5.0"}
WEATHER_KEYWORDS = [
    "precipitation", "temperature", "rainfall", "snowfall",
    "inches of", "degrees", "fahrenheit", "celsius", "weather",
    "humidity", "wind speed", "snow", "rain", "storm", "hurricane",
    "tornado", "flood", "drought", "heat wave"
]

all_weather = []
offset = 0
limit = 100

print("Paginating Polymarket events...")
while True:
    r = requests.get("https://gamma-api.polymarket.com/events",
        params={"active": "true", "closed": "false", "limit": limit, "offset": offset},
        headers=HEADERS, timeout=15)
    if not r.ok:
        print(f"Error at offset {offset}: {r.status_code}")
        break
    events = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
    if not events:
        break

    for e in events:
        title = e.get("title", "").lower()
        desc = e.get("description", "").lower()
        if any(kw in title or kw in desc for kw in WEATHER_KEYWORDS):
            all_weather.append({
                "slug": e.get("slug"),
                "title": e.get("title"),
                "markets": len(e.get("markets", [])),
                "volume": sum(float(m.get("volume") or 0) for m in e.get("markets", []))
            })

    print(f"  offset={offset}: {len(events)} events scanned, {len(all_weather)} weather found so far")
    offset += limit
    if len(events) < limit or offset > 10000:
        break

print(f"\nTotal weather events found: {len(all_weather)}")
print("\nSlug list (sorted by volume):")
all_weather.sort(key=lambda x: x["volume"], reverse=True)
for e in all_weather:
    print(f'    "{e["slug"]}",  # {e["title"]} ({e["markets"]}m, ${e["volume"]:.0f})')

# Save
with open("discovered_slugs.json", "w") as f:
    json.dump(all_weather, f, indent=2)
print("\nSaved to discovered_slugs.json")
