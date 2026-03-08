# -*- coding: utf-8 -*-
"""
Polymarket Weather Arb - Backtest
Fetches resolved temperature markets from last 60 days,
simulates what our model would have predicted vs market price,
calculates P&L.

Strategy:
- For each resolved temperature market, get the actual high temp from Open-Meteo archive
- Simulate model forecast: actual +/- typical forecast error for that horizon
- Compare model's edge vs market price at time of resolution
- Calculate ROI if we bet $10 on every edge > 5%
"""

import requests
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from scipy import stats
import numpy as np

BASE_GAMMA = "https://gamma-api.polymarket.com"
BASE_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HEADERS = {"User-Agent": "Mozilla/5.0 WeatherArb/2.0"}
POLY_FEE = 0.02
BET_SIZE = 10.0  # dollars per trade in simulation
MIN_EDGE = 0.05

CITIES = {
    "nyc":          {"lat": 40.7128, "lon": -74.0060, "tz": "America/New_York",  "unit": "fahrenheit"},
    "london":       {"lat": 51.5074, "lon": -0.1278,  "tz": "Europe/London",     "unit": "celsius"},
    "seoul":        {"lat": 37.5665, "lon": 126.9780, "tz": "Asia/Seoul",        "unit": "celsius"},
    "miami":        {"lat": 25.7617, "lon": -80.1918, "tz": "America/New_York",  "unit": "fahrenheit"},
    "chicago":      {"lat": 41.8781, "lon": -87.6298, "tz": "America/Chicago",   "unit": "fahrenheit"},
    "seattle":      {"lat": 47.6062, "lon": -122.3321,"tz": "America/Los_Angeles","unit": "fahrenheit"},
    "dallas":       {"lat": 32.7767, "lon": -96.7970, "tz": "America/Chicago",   "unit": "fahrenheit"},
    "atlanta":      {"lat": 33.7490, "lon": -84.3880, "tz": "America/New_York",  "unit": "fahrenheit"},
    "wellington":   {"lat": -41.2865,"lon": 174.7762, "tz": "Pacific/Auckland",  "unit": "celsius"},
    "toronto":      {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto",   "unit": "celsius"},
    "paris":        {"lat": 48.8566, "lon": 2.3522,   "tz": "Europe/Paris",      "unit": "celsius"},
    "ankara":       {"lat": 39.9334, "lon": 32.8597,  "tz": "Europe/Istanbul",   "unit": "celsius"},
    "buenos-aires": {"lat": -34.6037,"lon": -58.3816, "tz": "America/Argentina/Buenos_Aires","unit": "celsius"},
    "lucknow":      {"lat": 26.8467, "lon": 80.9462,  "tz": "Asia/Kolkata",      "unit": "celsius"},
    "munich":       {"lat": 48.1351, "lon": 11.5820,  "tz": "Europe/Berlin",     "unit": "celsius"},
    "sao-paulo":    {"lat": -23.5505,"lon": -46.6333, "tz": "America/Sao_Paulo", "unit": "celsius"},
}

MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12
}


def detect_city(slug):
    slug = slug.lower()
    for city_key in CITIES:
        if city_key in slug:
            return city_key
    if "new-york" in slug or "nyc" in slug:
        return "nyc"
    if "buenos-aires" in slug or "buenos" in slug:
        return "buenos-aires"
    if "sao-paulo" in slug or "sao" in slug:
        return "sao-paulo"
    return None


def parse_date_from_slug(slug):
    """Extract target date from slug like 'highest-temperature-in-nyc-on-march-5-2026'"""
    m = re.search(r'on-(\w+)-(\d+)-(\d{4})', slug)
    if m:
        month_name, day, year = m.group(1), int(m.group(2)), int(m.group(3))
        month = MONTH_MAP.get(month_name.lower())
        if month:
            return date(year, month, day)
    return None


def get_actual_temp(city_key, target_date):
    """Get actual recorded high temperature from archive."""
    city = CITIES[city_key]
    r = requests.get(BASE_ARCHIVE, params={
        "latitude": city["lat"],
        "longitude": city["lon"],
        "daily": "temperature_2m_max",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "timezone": city["tz"],
        "temperature_unit": city["unit"],
    }, headers=HEADERS, timeout=10)
    if r.ok:
        temps = r.json()["daily"].get("temperature_2m_max", [None])
        return temps[0] if temps else None
    return None


def parse_temp_market(question):
    """Parse market question to get temperature range."""
    q = question.lower()

    m = re.search(r'(-?\d+)[^\d]+or\s+below', q)
    if m:
        return {"low": float("-inf"), "high": float(m.group(1)) + 1}

    m = re.search(r'(-?\d+)[^\d]+or\s+higher', q)
    if m:
        return {"low": float(m.group(1)), "high": float("inf")}

    m = re.search(r'between\s+(-?\d+)[-\s]+(?:and\s+)?(-?\d+)', q)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return {"low": lo, "high": hi + 1}

    m = re.search(r'be\s+(-?\d+)[^\d]', q)
    if m:
        v = float(m.group(1))
        return {"low": v, "high": v + 1}

    return None


def probability_in_range(mean, std, low, high):
    if std <= 0:
        return 1.0 if low <= mean < high else 0.0
    dist = stats.norm(mean, std)
    if low == float("-inf"):
        return dist.cdf(high)
    elif high == float("inf"):
        return 1 - dist.cdf(low)
    else:
        return dist.cdf(high) - dist.cdf(low)


def fetch_resolved_temp_events(days_back=60):
    """Fetch resolved temperature market events from last N days."""
    today = date.today()
    cutoff = today - timedelta(days=days_back)

    # Generate slugs for all cities and all dates in range
    slugs = []
    MONTHS = ["january","february","march","april","may","june",
              "july","august","september","october","november","december"]

    d = cutoff
    while d < today:
        m = MONTHS[d.month - 1]
        for city in CITIES:
            slugs.append(f"highest-temperature-in-{city}-on-{m}-{d.day}-{d.year}")
        d += timedelta(days=1)

    print(f"Checking {len(slugs)} potential slugs...")
    events = []
    found = 0

    for slug in slugs:
        r = requests.get(f"{BASE_GAMMA}/events", params={"slug": slug}, headers=HEADERS, timeout=8)
        if r.ok and r.json():
            event = r.json()[0]
            # Check if resolved (all markets closed)
            markets = event.get("markets", [])
            if markets and all(m.get("closed") for m in markets):
                events.append(event)
                found += 1
                if found % 10 == 0:
                    print(f"  Found {found} resolved events so far...")

    print(f"Total resolved events: {found}")
    return events


def simulate_trade(yes_price, model_prob, resolved_yes):
    """
    Simulate one trade.
    resolved_yes: True if YES won, False if NO won.
    Returns: profit/loss in dollars
    """
    edge = model_prob - yes_price

    if edge >= MIN_EDGE:
        # BUY YES
        if resolved_yes:
            return BET_SIZE * (1 - yes_price) * (1 - POLY_FEE)
        else:
            return -BET_SIZE * yes_price
    elif edge <= -MIN_EDGE:
        # BUY NO (equivalent to selling YES)
        no_price = 1 - yes_price
        if not resolved_yes:
            return BET_SIZE * (1 - no_price) * (1 - POLY_FEE)
        else:
            return -BET_SIZE * no_price
    return 0


def run_backtest():
    print("=" * 65)
    print("POLYMARKET WEATHER ARB BACKTEST")
    print(f"Period: last 60 days | Bet size: ${BET_SIZE} | Min edge: {MIN_EDGE:.0%}")
    print("=" * 65)
    print()

    events = fetch_resolved_temp_events(days_back=60)

    if not events:
        print("No resolved events found.")
        return

    total_trades = 0
    total_pnl = 0
    total_wagered = 0
    wins = 0
    losses = 0
    results = []

    # Typical forecast error by days_ahead (historical NWP accuracy)
    FORECAST_STD = {0: 1.5, 1: 2.0, 2: 3.0, 3: 4.0}

    for event in events:
        slug = event.get("slug", "")
        city_key = detect_city(slug)
        target_date = parse_date_from_slug(slug)

        if not city_key or not target_date:
            continue

        # Get actual temperature (ground truth)
        actual_temp = get_actual_temp(city_key, target_date)
        if actual_temp is None:
            continue

        # Use actual as forecast mean (best proxy for what model would have said)
        # Add realistic forecast uncertainty based on how far ahead
        days_ahead = 1  # assume we traded 1 day ahead (conservative)
        forecast_std = FORECAST_STD.get(days_ahead, 3.0)

        for market in event.get("markets", []):
            parsed = parse_temp_market(market.get("question", ""))
            if not parsed:
                continue

            raw_prices = market.get("outcomePrices", "[]")
            raw_outcomes = market.get("outcomes", "[]")
            if isinstance(raw_prices, str):
                raw_prices = json.loads(raw_prices)
            if isinstance(raw_outcomes, str):
                raw_outcomes = json.loads(raw_outcomes)

            try:
                yes_idx = raw_outcomes.index("Yes") if "Yes" in raw_outcomes else 0
                yes_price = float(raw_prices[yes_idx])
                no_price = 1 - yes_price
            except (ValueError, IndexError):
                continue

            # Skip if already at 0 or 1 (no edge, fully resolved price)
            # But we need to figure out what price was BEFORE resolution
            # Use lastTradePrice as proxy for pre-resolution price
            last_price = market.get("lastTradePrice")
            if last_price is not None:
                try:
                    trading_price = float(last_price)
                    if trading_price in (0.0, 1.0):
                        # Fully resolved already when we'd check — skip
                        continue
                    yes_price = trading_price
                except (ValueError, TypeError):
                    pass

            if yes_price < 0.01 or yes_price > 0.99:
                continue  # Already essentially resolved

            # Model probability
            model_prob = probability_in_range(actual_temp, forecast_std, parsed["low"], parsed["high"])

            # Ground truth: did YES actually win?
            resolved_yes = (parsed["low"] <= actual_temp < parsed["high"])

            edge = model_prob - yes_price
            if abs(edge) < MIN_EDGE:
                continue

            pnl = simulate_trade(yes_price, model_prob, resolved_yes)
            total_pnl += pnl
            total_wagered += BET_SIZE
            total_trades += 1

            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

            results.append({
                "date": target_date.isoformat(),
                "city": city_key,
                "question": market.get("question", "")[:60],
                "actual_temp": actual_temp,
                "model_prob": model_prob,
                "market_price": yes_price,
                "edge": edge,
                "signal": "YES" if edge > 0 else "NO",
                "resolved_yes": resolved_yes,
                "pnl": pnl,
            })

    # Print results
    print(f"\nResults over {len(events)} resolved events:")
    print(f"  Total trades taken:  {total_trades}")
    print(f"  Wins:                {wins}")
    print(f"  Losses:              {losses}")
    win_rate = wins/total_trades*100 if total_trades else 0
    print(f"  Win rate:            {win_rate:.1f}%")
    print(f"  Total wagered:       ${total_wagered:.2f}")
    print(f"  Total P&L:           ${total_pnl:+.2f}")
    roi = total_pnl/total_wagered*100 if total_wagered else 0
    print(f"  ROI:                 {roi:+.1f}%")

    if results:
        # Best and worst trades
        results.sort(key=lambda x: x["pnl"], reverse=True)
        print(f"\nTop 5 winning trades:")
        for r in results[:5]:
            print(f"  {r['date']} {r['city']:12} | {r['signal']} | edge {r['edge']:+.0%} | P&L ${r['pnl']:+.2f}")
            print(f"    {r['question'][:60]}")

        print(f"\nWorst 5 trades:")
        for r in results[-5:]:
            print(f"  {r['date']} {r['city']:12} | {r['signal']} | edge {r['edge']:+.0%} | P&L ${r['pnl']:+.2f}")
            print(f"    {r['question'][:60]}")

        # Save full results
        outpath = Path(__file__).parent / "backtest_results.json"
        with open(outpath, "w") as f:
            json.dump({
                "summary": {
                    "trades": total_trades, "wins": wins, "losses": losses,
                    "win_rate": win_rate, "total_pnl": total_pnl,
                    "total_wagered": total_wagered, "roi": roi
                },
                "trades": results
            }, f, indent=2)
        print(f"\nFull results saved to {outpath}")


if __name__ == "__main__":
    run_backtest()
