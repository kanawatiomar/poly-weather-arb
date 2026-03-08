# -*- coding: utf-8 -*-
"""
Polymarket Weather Arbitrage Scanner v2
- Handles: monthly precipitation + daily temperature markets
- Outputs: edge table + saves scan_results.json
"""

import requests
import json
import os
import sys
from datetime import datetime, timedelta, date
from scipy import stats
import numpy as np

BASE_GAMMA = "https://gamma-api.polymarket.com"
BASE_WEATHER = "https://api.open-meteo.com/v1/forecast"
BASE_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HEADERS = {"User-Agent": "Mozilla/5.0 WeatherArb/2.0"}
POLY_FEE = 0.02

CITIES = {
    "nyc":         {"lat": 40.7128, "lon": -74.0060, "tz": "America/New_York", "unit": "fahrenheit"},
    "london":      {"lat": 51.5074, "lon": -0.1278,  "tz": "Europe/London",    "unit": "celsius"},
    "seoul":       {"lat": 37.5665, "lon": 126.9780, "tz": "Asia/Seoul",       "unit": "celsius"},
    "miami":       {"lat": 25.7617, "lon": -80.1918, "tz": "America/New_York", "unit": "fahrenheit"},
    "chicago":     {"lat": 41.8781, "lon": -87.6298, "tz": "America/Chicago",  "unit": "fahrenheit"},
    "seattle":     {"lat": 47.6062, "lon": -122.3321,"tz": "America/Los_Angeles","unit": "fahrenheit"},
    "dallas":      {"lat": 32.7767, "lon": -96.7970, "tz": "America/Chicago",  "unit": "fahrenheit"},
    "atlanta":     {"lat": 33.7490, "lon": -84.3880, "tz": "America/New_York", "unit": "fahrenheit"},
    "wellington":  {"lat": -41.2865,"lon": 174.7762, "tz": "Pacific/Auckland", "unit": "celsius"},
    "toronto":     {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto",  "unit": "celsius"},
    "paris":       {"lat": 48.8566, "lon": 2.3522,   "tz": "Europe/Paris",    "unit": "celsius"},
    "ankara":      {"lat": 39.9334, "lon": 32.8597,  "tz": "Europe/Istanbul", "unit": "celsius"},
    "buenos-aires":{"lat": -34.6037,"lon": -58.3816, "tz": "America/Argentina/Buenos_Aires","unit": "celsius"},
    "lucknow":     {"lat": 26.8467, "lon": 80.9462,  "tz": "Asia/Kolkata",    "unit": "celsius"},
    "munich":      {"lat": 48.1351, "lon": 11.5820,  "tz": "Europe/Berlin",   "unit": "celsius"},
    "sao-paulo":   {"lat": -23.5505,"lon": -46.6333, "tz": "America/Sao_Paulo","unit": "celsius"},
    "la":          {"lat": 34.0522, "lon": -118.2437,"tz": "America/Los_Angeles","unit": "fahrenheit"},
}

def detect_city(slug_or_title):
    txt = (slug_or_title or "").lower()
    for city_key in CITIES:
        if city_key in txt:
            return city_key
    if "new york" in txt or "nyc" in txt:
        return "nyc"
    if "los angeles" in txt:
        return "la"
    if "buenos aires" in txt:
        return "buenos-aires"
    if "sao paulo" in txt:
        return "sao-paulo"
    return None


def fetch_event(slug):
    r = requests.get(f"{BASE_GAMMA}/events", params={"slug": slug}, headers=HEADERS, timeout=10)
    if r.ok and r.json():
        return r.json()[0]
    return None


def parse_temp_market(question):
    """
    Parse temp market question to extract range/bound.
    E.g. 'Will the highest temperature in NYC be between 40-41F on...' -> {low:40, high:42}
    'Will the highest temperature be 33F or below' -> {low:-inf, high:33}
    'Will the highest temperature be 48F or higher' -> {low:48, high:inf}
    """
    import re
    q = question.lower()

    # "X or below" (check before between/single to avoid false matches)
    m = re.search(r'(-?\d+)[^\d]+or\s+below', q)
    if m:
        return {"low": float("-inf"), "high": float(m.group(1)) + 1}

    # "X or higher"
    m = re.search(r'(-?\d+)[^\d]+or\s+higher', q)
    if m:
        return {"low": float(m.group(1)), "high": float("inf")}

    # "between X-Y" or "between X and Y" (e.g. "between 40-41F" or "between 40 and 41")
    m = re.search(r'between\s+(-?\d+)[-\s]+(?:and\s+)?(-?\d+)', q)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return {"low": lo, "high": hi + 1}  # inclusive range (40-41 means 40 <= x <= 41)

    # single value "be X°C/F on" - last resort
    m = re.search(r'be\s+(-?\d+)[^\d]', q)
    if m:
        v = float(m.group(1))
        return {"low": v, "high": v + 1}

    return None


def parse_precip_market(question):
    import re
    q = question.lower()
    m = re.search(r'between\s+([\d.]+)\s+and\s+([\d.]+)', q)
    if m:
        return {"low": float(m.group(1)), "high": float(m.group(2))}
    m = re.search(r'(?:more|greater)\s+than\s+([\d.]+)', q)
    if m:
        return {"low": float(m.group(1)), "high": float("inf")}
    m = re.search(r'less\s+than\s+([\d.]+)', q)
    if m:
        return {"low": float("-inf"), "high": float(m.group(1))}
    return None


def get_temperature_forecast(city_key, target_date):
    """
    Get the forecasted high temperature for a city on target_date.
    Returns: (mean_temp, std_dev)
    """
    city = CITIES.get(city_key)
    if not city:
        return None, None

    temp_unit = city["unit"]
    r = requests.get(BASE_WEATHER, params={
        "latitude": city["lat"],
        "longitude": city["lon"],
        "daily": "temperature_2m_max",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "timezone": city["tz"],
        "temperature_unit": temp_unit,
    }, headers=HEADERS, timeout=10)

    if r.ok:
        data = r.json()
        temps = data["daily"].get("temperature_2m_max", [None])
        t = temps[0] if temps else None
        if t is not None:
            # Std dev based on forecast horizon
            days_ahead = (target_date - date.today()).days
            if days_ahead <= 1:
                std = 1.5   # same/next day: very accurate
            elif days_ahead <= 3:
                std = 2.5
            elif days_ahead <= 7:
                std = 3.5
            else:
                std = 5.0
            return float(t), std
    return None, None


def get_precipitation_model(city_key, month_start, month_end):
    """
    Build a precipitation model for the month.
    Returns: (mean_inches, std_inches)
    """
    city = CITIES.get(city_key)
    if not city:
        return None, None

    today = date.today()
    month = month_start.month

    # 1. Historical (already happened)
    actual = 0.0
    if today > month_start:
        hist_end = min(today - timedelta(days=1), month_end)
        r = requests.get(BASE_ARCHIVE, params={
            "latitude": city["lat"], "longitude": city["lon"],
            "daily": "precipitation_sum",
            "start_date": month_start.isoformat(),
            "end_date": hist_end.isoformat(),
            "timezone": city["tz"],
            "precipitation_unit": "inch",
        }, headers=HEADERS, timeout=10)
        if r.ok:
            precip = r.json()["daily"].get("precipitation_sum", [])
            actual = sum(p for p in precip if p is not None)

    # 2. Forecast (today to +15 days)
    forecast_total, forecast_std = 0.0, 0.0
    max_forecast_end = None
    if today <= month_end:
        fc_start = max(today, month_start)
        fc_end = min(today + timedelta(days=15), month_end)
        max_forecast_end = fc_end
        r = requests.get(BASE_WEATHER, params={
            "latitude": city["lat"], "longitude": city["lon"],
            "daily": "precipitation_sum",
            "start_date": fc_start.isoformat(),
            "end_date": fc_end.isoformat(),
            "timezone": city["tz"],
            "precipitation_unit": "inch",
        }, headers=HEADERS, timeout=10)
        if r.ok:
            precip = r.json()["daily"].get("precipitation_sum", [])
            vals = [p for p in precip if p is not None]
            forecast_total = sum(vals)
            forecast_std = forecast_total * 0.15

    # 3. Climate normal for remaining days
    climate_mean, climate_std = 0.0, 0.0
    remaining_start = (max_forecast_end.day + 1) if max_forecast_end else month_start.day
    remaining_end = month_end.day
    if remaining_start <= remaining_end:
        samples = []
        for yr in range(date.today().year - 3, date.today().year):
            try:
                import calendar
                days_in = calendar.monthrange(yr, month)[1]
                s = date(yr, month, remaining_start)
                e = date(yr, month, min(remaining_end, days_in))
                r = requests.get(BASE_ARCHIVE, params={
                    "latitude": city["lat"], "longitude": city["lon"],
                    "daily": "precipitation_sum",
                    "start_date": s.isoformat(), "end_date": e.isoformat(),
                    "timezone": city["tz"],
                    "precipitation_unit": "inch",
                }, headers=HEADERS, timeout=10)
                if r.ok:
                    precip = r.json()["daily"].get("precipitation_sum", [])
                    samples.append(sum(p for p in precip if p is not None))
            except:
                pass
        if samples:
            climate_mean = np.mean(samples)
            climate_std = np.std(samples)

    total_mean = actual + forecast_total + climate_mean
    total_std = float(np.sqrt(forecast_std**2 + climate_std**2))
    return total_mean, total_std


def probability_in_range(mean, std, low, high):
    if std <= 0:
        return 1.0 if low <= mean < high else 0.0
    dist = stats.norm(mean, std)
    if low == float("-inf") and high == float("inf"):
        return 1.0
    elif low == float("-inf"):
        return dist.cdf(high)
    elif high == float("inf"):
        return 1 - dist.cdf(low)
    else:
        return dist.cdf(high) - dist.cdf(low)


def analyze_temperature_event(event, target_date):
    slug = event.get("slug", "")
    title = event.get("title", "")
    city_key = detect_city(slug)
    if not city_key:
        return []

    mean_temp, std_temp = get_temperature_forecast(city_key, target_date)
    if mean_temp is None:
        return []

    days_ahead = (target_date - date.today()).days
    unit = CITIES[city_key]["unit"]
    unit_sym = "F" if unit == "fahrenheit" else "C"

    print(f"\n{'='*60}")
    print(f"TEMP: {title}")
    print(f"City: {city_key} | Date: {target_date} | Forecast: {mean_temp:.1f}+/-{std_temp:.1f}{unit_sym} ({days_ahead}d ahead)")

    opportunities = []
    print(f"\n  {'Market':<52} {'Poly':>6} {'Model':>6} {'Edge':>7}  Signal")
    print(f"  {'-'*82}")

    for market in event.get("markets", []):
        if market.get("closed"):
            continue

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
        except (ValueError, IndexError):
            continue

        model_prob = probability_in_range(mean_temp, std_temp, parsed["low"], parsed["high"])
        edge_pct = model_prob - yes_price
        signal = ""
        if abs(edge_pct) >= 0.05:
            signal = "[BUY YES]" if edge_pct > 0 else "[BUY NO] "

        q_short = market.get("question", "")[:50]
        print(f"  {q_short:<52} {yes_price:>5.1%} {model_prob:>5.1%} {edge_pct:>+6.1%}  {signal}")

        if abs(edge_pct) >= 0.05:
            clob_ids = market.get("clobTokenIds", "[]")
            if isinstance(clob_ids, str):
                clob_ids = json.loads(clob_ids)
            outcomes_parsed = raw_outcomes

            # Get token ID for the signal
            signal_outcome = "Yes" if edge_pct > 0 else "No"
            try:
                sig_idx = outcomes_parsed.index(signal_outcome)
                token_id = clob_ids[sig_idx] if sig_idx < len(clob_ids) else None
            except (ValueError, IndexError):
                token_id = None

            opportunities.append({
                "type": "temperature",
                "event": title,
                "question": market.get("question", ""),
                "market_id": market.get("id"),
                "token_id": token_id,
                "signal_outcome": signal_outcome,
                "yes_price": yes_price,
                "model_prob": model_prob,
                "edge_pct": edge_pct,
                "signal": "YES" if edge_pct > 0 else "NO",
                "volume": float(market.get("volume") or 0),
                "forecast_mean": mean_temp,
                "forecast_std": std_temp,
                "city": city_key,
                "date": target_date.isoformat(),
                "days_ahead": days_ahead,
            })

    return opportunities


def discover_daily_temperature_slugs(today, days_ahead=3):
    """Generate slug patterns for today + next N days."""
    slugs = []
    MONTH_NAMES = ["january","february","march","april","may","june",
                   "july","august","september","october","november","december"]
    cities = list(CITIES.keys())

    for d in range(days_ahead + 1):
        target = today + timedelta(days=d)
        m = MONTH_NAMES[target.month - 1]
        for city in cities:
            slug = f"highest-temperature-in-{city}-on-{m}-{target.day}-{target.year}"
            slugs.append((slug, target))
    return slugs


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Weather Arb Scanner")
    parser.add_argument("--days", type=int, default=3, help="Days ahead to scan (default: 3)")
    parser.add_argument("--min-edge", type=float, default=0.05, help="Min edge to flag (default: 0.05)")
    parser.add_argument("--min-vol", type=float, default=500, help="Min market volume (default: $500)")
    parser.add_argument("--temp-only", action="store_true", help="Skip precipitation markets (avoids SSL issues)")
    args = parser.parse_args()

    today = date.today()
    print("[WeatherArb v2] Polymarket Weather Arbitrage Scanner")
    print(f"   Date: {today}  |  Scanning {args.days+1} days of temperature + active precipitation")
    print()

    all_opps = []

    # --- 1. Daily temperature markets ---
    print(f"[1] Daily temperature markets (today + {args.days} days)...")
    slugs = discover_daily_temperature_slugs(today, args.days)
    found = 0
    for slug, target_date in slugs:
        event = fetch_event(slug)
        if not event:
            continue
        found += 1
        opps = analyze_temperature_event(event, target_date)
        all_opps.extend(opps)
    print(f"    Found {found} active temperature events")

    # --- 2. Monthly precipitation markets ---
    if args.temp_only:
        print(f"\n[2] Skipping precipitation markets (--temp-only)")
    else:
      print(f"\n[2] Monthly precipitation markets...")
    PRECIP_SLUGS = [
        "precipitation-in-nyc-in-march",
        "precipitation-in-nyc-in-april",
        "precipitation-in-seattle-in-march",
        "precipitation-in-seattle-in-april",
        "precipitation-in-chicago-in-march",
        "precipitation-in-london-in-march",
        "precipitation-in-la-in-march",
    ]
    for slug in ([] if args.temp_only else PRECIP_SLUGS):
        event = fetch_event(slug)
        if not event:
            continue
        city_key = detect_city(slug)
        if not city_key:
            continue
        markets = [m for m in event.get("markets", []) if not m.get("closed")]
        if not markets:
            continue

        # Get end date from first market
        end_str = markets[0].get("endDate", "")[:10]
        if not end_str:
            continue
        end_date = date.fromisoformat(end_str)
        month_start = end_date.replace(day=1)

        mean, std = get_precipitation_model(city_key, month_start, end_date)
        if mean is None:
            continue

        print(f"\n{'='*60}")
        print(f"PRECIP: {event['title']}")
        print(f"City: {city_key} | Model: {mean:.2f}in +/-{std:.2f}in")

        print(f"\n  {'Market':<52} {'Poly':>6} {'Model':>6} {'Edge':>7}  Signal")
        print(f"  {'-'*82}")

        for market in markets:
            parsed = parse_precip_market(market.get("question", ""))
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
            except:
                continue

            model_prob = probability_in_range(mean, std, parsed["low"], parsed["high"])
            edge_pct = model_prob - yes_price
            signal = ""
            if abs(edge_pct) >= args.min_edge:
                signal = "[BUY YES]" if edge_pct > 0 else "[BUY NO] "

            q_short = market.get("question", "")[:50]
            print(f"  {q_short:<52} {yes_price:>5.1%} {model_prob:>5.1%} {edge_pct:>+6.1%}  {signal}")

            if abs(edge_pct) >= args.min_edge:
                clob_ids = market.get("clobTokenIds", "[]")
                if isinstance(clob_ids, str):
                    clob_ids = json.loads(clob_ids)
                signal_outcome = "Yes" if edge_pct > 0 else "No"
                try:
                    sig_idx = raw_outcomes.index(signal_outcome)
                    token_id = clob_ids[sig_idx] if sig_idx < len(clob_ids) else None
                except:
                    token_id = None

                all_opps.append({
                    "type": "precipitation",
                    "event": event["title"],
                    "question": market.get("question", ""),
                    "market_id": market.get("id"),
                    "token_id": token_id,
                    "signal_outcome": signal_outcome,
                    "yes_price": yes_price,
                    "model_prob": model_prob,
                    "edge_pct": edge_pct,
                    "signal": "YES" if edge_pct > 0 else "NO",
                    "volume": float(market.get("volume") or 0),
                    "city": city_key,
                })

    # --- Summary ---
    print(f"\n{'='*60}")
    # Filter by min vol
    actionable = [o for o in all_opps if o["volume"] >= args.min_vol]
    print(f"[RESULTS] Total edges found: {len(all_opps)} | Actionable (>${args.min_vol:.0f} vol): {len(actionable)}")
    print()

    if actionable:
        actionable.sort(key=lambda x: abs(x["edge_pct"]), reverse=True)
        print(f"  {'Signal':<10} {'Edge':>7}  {'Poly':>6}  {'Model':>6}  {'Vol':>8}  Question")
        print(f"  {'-'*90}")
        for opp in actionable[:20]:
            tid_short = (opp.get("token_id") or "N/A")[:12]
            q_short = opp["question"][:55]
            print(f"  {opp['signal']:<10} {opp['edge_pct']:>+6.1%}  {opp['yes_price']:>5.1%}  {opp['model_prob']:>5.1%}  ${opp['volume']:>7.0f}  {q_short}")
            if opp.get("token_id"):
                print(f"  {'':10}   token: {opp['token_id'][:40]}")

    # Save
    out = {
        "timestamp": datetime.now().isoformat(),
        "date": today.isoformat(),
        "total_edges": len(all_opps),
        "actionable": len(actionable),
        "opportunities": actionable,
    }
    outpath = Path(__file__).parent / "scan_results.json"
    with open(outpath, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to {outpath}")


from pathlib import Path
if __name__ == "__main__":
    main()
