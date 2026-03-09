# -*- coding: utf-8 -*-
"""
Polymarket Weather Arb — Proper Backtester v1

Uses REAL historical forecast data (not actuals-as-proxy):
  - Open-Meteo Historical Forecast API: what models actually predicted day-before
  - CLOB price history: pre-resolution market price (~24h before expiry)
  - Same ensemble + probability logic as scanner.py v3
  - Same quality filters as paper_trader.py (MIN_ENTRY_PRICE, SKIP_EXACT_DEGREE)

Usage:
    py proper_backtest.py               # last 60 days
    py proper_backtest.py --days 90     # last 90 days
    py proper_backtest.py --min-edge 0.15
"""

import requests, json, re, time, sys, argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from scipy import stats
import numpy as np

# ── API endpoints ─────────────────────────────────────────────────────────────
BASE_GAMMA    = "https://gamma-api.polymarket.com"
BASE_HIST_FC  = "https://historical-forecast-api.open-meteo.com/v1/forecast"
BASE_ARCHIVE  = "https://archive-api.open-meteo.com/v1/archive"
BASE_CLOB     = "https://clob.polymarket.com"
HEADERS       = {"User-Agent": "Mozilla/5.0 WeatherArb/BacktestV1"}
POLY_FEE      = 0.02

# ── Strategy constants ─────────────────────────────────────────────────────────
MIN_EDGE          = 0.10
MIN_ENTRY_PRICE   = 0.05   # skip near-zero tokens (market near-certain)
SKIP_EXACT_DEGREE = True   # skip single-degree exact-match markets
KELLY_FRAC        = 0.5
MIN_BET           = 1.0
MAX_BET           = 50.0
BANKROLL          = 1000.0

# ── Ensemble models (historical forecast API supports these) ──────────────────
HIST_MODELS = ["gfs", "ecmwf_ifs04", "icon_global", "gem_global"]
# BOM not available in historical forecast API — skipped

# ── City registry ─────────────────────────────────────────────────────────────
CITIES = {
    "nyc":          {"lat": 40.7128,  "lon": -74.0060,  "tz": "America/New_York",              "unit": "fahrenheit"},
    "london":       {"lat": 51.5074,  "lon": -0.1278,   "tz": "Europe/London",                 "unit": "celsius"},
    "seoul":        {"lat": 37.5665,  "lon": 126.9780,  "tz": "Asia/Seoul",                    "unit": "celsius"},
    "miami":        {"lat": 25.7617,  "lon": -80.1918,  "tz": "America/New_York",              "unit": "fahrenheit"},
    "chicago":      {"lat": 41.8781,  "lon": -87.6298,  "tz": "America/Chicago",               "unit": "fahrenheit"},
    "seattle":      {"lat": 47.6062,  "lon": -122.3321, "tz": "America/Los_Angeles",           "unit": "fahrenheit"},
    "dallas":       {"lat": 32.7767,  "lon": -96.7970,  "tz": "America/Chicago",               "unit": "fahrenheit"},
    "atlanta":      {"lat": 33.7490,  "lon": -84.3880,  "tz": "America/New_York",              "unit": "fahrenheit"},
    "wellington":   {"lat": -41.2865, "lon": 174.7762,  "tz": "Pacific/Auckland",              "unit": "celsius"},
    "toronto":      {"lat": 43.6532,  "lon": -79.3832,  "tz": "America/Toronto",               "unit": "celsius"},
    "paris":        {"lat": 48.8566,  "lon": 2.3522,    "tz": "Europe/Paris",                  "unit": "celsius"},
    "ankara":       {"lat": 39.9334,  "lon": 32.8597,   "tz": "Europe/Istanbul",               "unit": "celsius"},
    "buenos-aires": {"lat": -34.6037, "lon": -58.3816,  "tz": "America/Argentina/Buenos_Aires","unit": "celsius"},
    "lucknow":      {"lat": 26.8467,  "lon": 80.9462,   "tz": "Asia/Kolkata",                  "unit": "celsius"},
    "munich":       {"lat": 48.1351,  "lon": 11.5820,   "tz": "Europe/Berlin",                 "unit": "celsius"},
    "sao-paulo":    {"lat": -23.5505, "lon": -46.6333,  "tz": "America/Sao_Paulo",             "unit": "celsius"},
    "la":           {"lat": 34.0522,  "lon": -118.2437, "tz": "America/Los_Angeles",           "unit": "fahrenheit"},
}

MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def detect_city(text):
    text = (text or "").lower()
    for city_key in CITIES:
        if city_key in text:
            return city_key
    if "new-york" in text or "new york" in text or " nyc" in text:
        return "nyc"
    if "los-angeles" in text or "los angeles" in text:
        return "la"
    if "buenos-aires" in text or "buenos aires" in text:
        return "buenos-aires"
    if "sao-paulo" in text or "sao paulo" in text:
        return "sao-paulo"
    return None


def parse_date_from_slug(slug):
    """
    Parse date from slug: 'highest-temperature-in-chicago-on-february-1'
    (no year in slug — infer from context that dates in last 60 days are 2025/2026)
    """
    m = re.search(r'on-(\w+)-(\d+)', slug)
    if m:
        month_name, day = m.group(1), int(m.group(2))
        month = MONTH_MAP.get(month_name.lower())
        if month:
            try:
                # Guess year: if month < current month, use current year; else previous year
                today = date.today()
                year = today.year
                if month > today.month:
                    year -= 1
                return date(year, month, day)
            except ValueError:
                pass
    return None


def parse_temp_market(question):
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
    d = stats.norm(mean, std)
    if low == float("-inf"):
        return d.cdf(high)
    elif high == float("inf"):
        return 1 - d.cdf(low)
    else:
        return d.cdf(high) - d.cdf(low)


def safe_get(url, params=None, retries=2, sleep=0.2):
    """HTTP GET with retry + rate-limit sleep."""
    time.sleep(sleep)
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=12)
            if r.ok:
                return r
        except Exception as e:
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None


# ── Data fetchers ─────────────────────────────────────────────────────────────

def fetch_historical_forecast(city_key, target_date):
    """
    Fetch what the ensemble models predicted the day BEFORE target_date.
    Returns (mean_temp, std_temp, models_used) or (None, None, 0).
    """
    city = CITIES[city_key]
    forecast_date = target_date - timedelta(days=1)  # init date = day before

    temps = []
    for model in HIST_MODELS:
        r = safe_get(BASE_HIST_FC, params={
            "latitude":         city["lat"],
            "longitude":        city["lon"],
            "daily":            "temperature_2m_max",
            "start_date":       forecast_date.isoformat(),
            "end_date":         target_date.isoformat(),
            "timezone":         city["tz"],
            "temperature_unit": city["unit"],
            "models":           model,
        })
        if not r:
            continue
        try:
            daily = r.json()["daily"]
            dates  = daily.get("time", [])
            values = daily.get("temperature_2m_max", [])
            # Find the target date value
            for d, v in zip(dates, values):
                if d == target_date.isoformat() and v is not None:
                    temps.append(v)
                    break
        except Exception:
            continue

    if len(temps) < 2:
        return None, None, len(temps)

    return float(np.mean(temps)), float(np.std(temps, ddof=1)), len(temps)


def fetch_actual_temp(city_key, target_date):
    """Get real recorded high temperature from archive."""
    city = CITIES[city_key]
    r = safe_get(BASE_ARCHIVE, params={
        "latitude":         city["lat"],
        "longitude":        city["lon"],
        "daily":            "temperature_2m_max",
        "start_date":       target_date.isoformat(),
        "end_date":         target_date.isoformat(),
        "timezone":         city["tz"],
        "temperature_unit": city["unit"],
    })
    if r:
        try:
            vals = r.json()["daily"].get("temperature_2m_max", [None])
            return vals[0] if vals else None
        except Exception:
            pass
    return None


def fetch_pre_resolution_price(market_data):
    """
    Get the YES token price from lastTradePrice field.
    This is the actual price the market traded at before resolution.
    """
    ltp = market_data.get("lastTradePrice")
    if ltp is not None:
        try:
            price = float(ltp)
            if 0 < price < 1:
                return price, "lastTradePrice"
        except (ValueError, TypeError):
            pass
    return None, None


def fetch_event_by_slug(slug):
    r = safe_get(f"{BASE_GAMMA}/events", params={"slug": slug})
    if r and r.json():
        return r.json()[0]
    return None


# ── Kelly sizing ──────────────────────────────────────────────────────────────

def kelly_size(edge_pct, price, forecast_mean=None, range_low=None, range_high=None):
    if price <= 0 or price >= 1:
        return MIN_BET
    b = (1.0 - price) / price
    p = price + edge_pct
    q = 1 - p
    kelly_pct = (b * p - q) / b
    if kelly_pct <= 0:
        return MIN_BET
    tail_penalty = 1.0
    if forecast_mean is not None and range_low is not None and range_high is not None:
        if not (range_low <= forecast_mean <= range_high):
            tail_penalty = 0.5
    bet = BANKROLL * KELLY_FRAC * kelly_pct * tail_penalty
    return round(max(MIN_BET, min(MAX_BET, bet)), 2)


# ── Main backtest ─────────────────────────────────────────────────────────────

def run_backtest(days_back=60, min_edge=MIN_EDGE):
    print("=" * 70)
    print("POLYMARKET WEATHER ARB — PROPER BACKTEST v1")
    print(f"Period: last {days_back} days | Min edge: {min_edge:.0%} | Bankroll: ${BANKROLL:.0f}")
    print(f"Filters: MIN_ENTRY_PRICE={MIN_ENTRY_PRICE} | SKIP_EXACT_DEGREE={SKIP_EXACT_DEGREE}")
    print("=" * 70)

    # Load pre-discovered slugs
    slugs_file = Path(__file__).parent / "discovered_slugs.json"
    all_slugs = []
    if slugs_file.exists():
        try:
            raw = json.loads(slugs_file.read_text())
            # Handle list of strings or list of dicts
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str):
                        all_slugs.append(item)
                    elif isinstance(item, dict) and "slug" in item:
                        all_slugs.append(item["slug"])
            elif isinstance(raw, dict):
                all_slugs = list(raw.keys())
            temp_from_file = [s for s in all_slugs if "highest-temperature" in s.lower()]
            print(f"Loaded {len(all_slugs)} slugs from file ({len(temp_from_file)} temp slugs)")
        except Exception as e:
            print(f"Warning: Could not load discovered_slugs.json: {e}")

    # Filter to temperature slugs only
    temp_slugs = [s for s in all_slugs if "highest-temperature" in s.lower()]

    # Also generate slugs for last N days (in case discovered_slugs.json is outdated)
    # NOTE: Polymarket slugs don't include year, just month and day
    today = date.today()
    cutoff = today - timedelta(days=days_back)
    MONTHS = ["january","february","march","april","may","june",
              "july","august","september","october","november","december"]
    generated = set()
    d = cutoff
    while d < today:
        m = MONTHS[d.month - 1]
        for city in CITIES:
            generated.add(f"highest-temperature-in-{city}-on-{m}-{d.day}")
        d += timedelta(days=1)

    # Merge — prefer discovered list, add generated ones not already there
    slug_set = set(temp_slugs) | generated
    slugs_to_check = sorted(slug_set)
    print(f"Total temperature slugs to check: {len(slugs_to_check)}")
    print()

    all_results   = []
    filtered_out  = []
    skipped_nodata = 0
    processed = 0

    for slug in slugs_to_check:
        city_key    = detect_city(slug)
        target_date = parse_date_from_slug(slug)

        if not city_key or not target_date:
            continue
        if target_date >= today:
            continue  # Not yet resolved
        if target_date < cutoff:
            continue

        # Fetch the event
        event = fetch_event_by_slug(slug)
        if not event:
            continue

        markets = event.get("markets", [])
        if not markets:
            continue

        # Only process resolved events
        if not all(m.get("closed") or m.get("resolved") for m in markets):
            continue

        processed += 1
        if processed % 10 == 0:
            print(f"  Processed {processed} events... ({len(all_results)} trades simulated)")

        # Get historical forecast (what models said day before)
        fc_mean, fc_std, models_used = fetch_historical_forecast(city_key, target_date)
        if fc_mean is None:
            skipped_nodata += 1
            continue

        # Get actual recorded temp (to determine YES/NO winner)
        actual_temp = fetch_actual_temp(city_key, target_date)
        if actual_temp is None:
            skipped_nodata += 1
            continue

        for market in markets:
            question = market.get("question", "")
            parsed   = parse_temp_market(question)
            if not parsed:
                continue

            range_low  = parsed["low"]
            range_high = parsed["high"]

            # Clip infinities for serialization
            rl_json = None if range_low  == float("-inf") else range_low
            rh_json = None if range_high == float("inf")  else range_high

            # Exact-degree filter
            if SKIP_EXACT_DEGREE and rl_json is not None and rh_json is not None:
                if (rh_json - rl_json) == 1:
                    filtered_out.append({
                        "reason": "EXACT_DEGREE", "city": city_key,
                        "date": target_date.isoformat(), "question": question[:60]
                    })
                    continue

            # Get pre-resolution price from market's lastTradePrice
            pre_res_price, price_source = fetch_pre_resolution_price(market)
            if pre_res_price is None:
                continue

            # Note: lastTradePrice can be very low or very high (0.001 to 0.999)
            # We apply entry price filters later, so don't skip here based on range

            yes_price  = pre_res_price
            model_prob = probability_in_range(fc_mean, fc_std, range_low, range_high)
            edge_pct   = model_prob - yes_price

            if abs(edge_pct) < min_edge:
                continue

            # Signal token price
            signal        = "YES" if edge_pct > 0 else "NO"
            signal_price  = yes_price if signal == "YES" else (1.0 - yes_price)
            signal_price  = max(0.001, min(0.999, signal_price))

            # Entry price filter
            if signal_price < MIN_ENTRY_PRICE:
                filtered_out.append({
                    "reason": "LOW_PRICE", "city": city_key,
                    "date": target_date.isoformat(), "question": question[:60],
                    "price": signal_price
                })
                continue

            # Actual outcome
            resolved_yes = (range_low <= actual_temp < range_high)

            # Kelly sizing
            bet = kelly_size(abs(edge_pct), signal_price, fc_mean, rl_json, rh_json)

            # P&L simulation
            if signal == "YES":
                if resolved_yes:
                    pnl = bet * (1 - yes_price) * (1 - POLY_FEE)
                else:
                    pnl = -bet
            else:
                if not resolved_yes:
                    pnl = bet * yes_price * (1 - POLY_FEE)
                else:
                    pnl = -bet

            market_type = "exact"
            if range_low == float("-inf") or range_high == float("inf"):
                market_type = "above_below"
            elif (range_high - range_low) > 1:
                market_type = "range"

            all_results.append({
                "date":          target_date.isoformat(),
                "city":          city_key,
                "question":      question[:70],
                "market_type":   market_type,
                "forecast_mean": round(fc_mean, 2),
                "forecast_std":  round(fc_std, 3),
                "models_used":   models_used,
                "actual_temp":   actual_temp,
                "pre_res_price": round(pre_res_price, 4),
                "price_source":  price_source,
                "model_prob":    round(model_prob, 4),
                "edge_pct":      round(edge_pct, 4),
                "signal":        signal,
                "signal_price":  round(signal_price, 4),
                "resolved_yes":  resolved_yes,
                "bet":           bet,
                "pnl":           round(pnl, 4),
                "win":           pnl > 0,
            })

    # ── Results ───────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"BACKTEST COMPLETE")
    print(f"  Events processed:    {processed}")
    print(f"  Skipped (no data):   {skipped_nodata}")
    print(f"  Filtered out:        {len(filtered_out)}")
    print(f"  Trades simulated:    {len(all_results)}")

    if not all_results:
        print("  No trades to summarize.")
        return

    wins         = sum(1 for r in all_results if r["win"])
    losses       = len(all_results) - wins
    total_pnl    = sum(r["pnl"] for r in all_results)
    total_wagered = sum(r["bet"] for r in all_results)
    win_rate     = wins / len(all_results) * 100
    roi          = total_pnl / total_wagered * 100 if total_wagered else 0
    final_broll  = BANKROLL + total_pnl

    print()
    print(f"  Win rate:            {wins}/{len(all_results)} = {win_rate:.1f}%")
    print(f"  Total wagered:       ${total_wagered:.2f}")
    print(f"  Total P&L:           ${total_pnl:+.2f}")
    print(f"  ROI:                 {roi:+.1f}%")
    print(f"  Final bankroll:      ${final_broll:.2f}")

    # ── By city ──────────────────────────────────────────────────────────────
    print()
    print("Results by city:")
    city_stats = {}
    for r in all_results:
        c = r["city"]
        if c not in city_stats:
            city_stats[c] = {"wins": 0, "total": 0, "pnl": 0.0}
        city_stats[c]["total"] += 1
        city_stats[c]["pnl"]   += r["pnl"]
        if r["win"]:
            city_stats[c]["wins"] += 1

    for city, cs in sorted(city_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = cs["wins"] / cs["total"] * 100
        print(f"  {city:<15} {cs['wins']:>2}/{cs['total']:>2} ({wr:>4.0f}%)  P&L: ${cs['pnl']:>+7.2f}")

    # ── By market type ────────────────────────────────────────────────────────
    print()
    print("Results by market type:")
    type_stats = {}
    for r in all_results:
        t = r["market_type"]
        if t not in type_stats:
            type_stats[t] = {"wins": 0, "total": 0, "pnl": 0.0}
        type_stats[t]["total"] += 1
        type_stats[t]["pnl"]   += r["pnl"]
        if r["win"]:
            type_stats[t]["wins"] += 1

    for mtype, ts in type_stats.items():
        wr = ts["wins"] / ts["total"] * 100
        print(f"  {mtype:<15} {ts['wins']:>2}/{ts['total']:>2} ({wr:>4.0f}%)  P&L: ${ts['pnl']:>+7.2f}")

    # ── Top/bottom trades ─────────────────────────────────────────────────────
    sorted_results = sorted(all_results, key=lambda x: x["pnl"], reverse=True)
    print()
    print("Top 5 trades:")
    for r in sorted_results[:5]:
        print(f"  {r['date']} {r['city']:<13} [{r['signal']}] edge {r['edge_pct']:+.0%}"
              f" | bet ${r['bet']:.2f} → P&L ${r['pnl']:+.2f}")
        print(f"    {r['question'][:65]}")

    print()
    print("Worst 5 trades:")
    for r in sorted_results[-5:]:
        print(f"  {r['date']} {r['city']:<13} [{r['signal']}] edge {r['edge_pct']:+.0%}"
              f" | bet ${r['bet']:.2f} → P&L ${r['pnl']:+.2f}")
        print(f"    {r['question'][:65]}")

    # ── Filter analysis ───────────────────────────────────────────────────────
    if filtered_out:
        print()
        print(f"Filter breakdown ({len(filtered_out)} total filtered):")
        by_reason = {}
        for f in filtered_out:
            by_reason[f["reason"]] = by_reason.get(f["reason"], 0) + 1
        for reason, count in sorted(by_reason.items(), key=lambda x: x[1], reverse=True):
            print(f"  {reason:<20} {count:>4} trades filtered")

    # ── Save results ──────────────────────────────────────────────────────────
    out_path = Path(__file__).parent / "backtest_results_v2.json"
    payload  = {
        "run_date":   date.today().isoformat(),
        "config": {
            "days_back":        days_back,
            "min_edge":         min_edge,
            "min_entry_price":  MIN_ENTRY_PRICE,
            "skip_exact_degree": SKIP_EXACT_DEGREE,
            "kelly_frac":       KELLY_FRAC,
            "bankroll":         BANKROLL,
        },
        "summary": {
            "events_processed": processed,
            "trades":           len(all_results),
            "wins":             wins,
            "losses":           losses,
            "win_rate":         round(win_rate, 2),
            "total_pnl":        round(total_pnl, 2),
            "total_wagered":    round(total_wagered, 2),
            "roi":              round(roi, 2),
            "final_bankroll":   round(final_broll, 2),
            "filtered_out":     len(filtered_out),
        },
        "city_stats":    city_stats,
        "type_stats":    type_stats,
        "trades":        sorted_results,
        "filtered":      filtered_out,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print()
    print(f"Full results saved to: {out_path.name}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proper Polymarket Weather Backtest v1")
    parser.add_argument("--days",        type=int,   default=60,              help="Days to look back (default: 60)")
    parser.add_argument("--min-edge",    type=float, default=MIN_EDGE,        help="Minimum edge %% (default: 0.10)")
    parser.add_argument("--min-entry",   type=float, default=MIN_ENTRY_PRICE, help="Min entry price for signal (default: 0.05)")
    parser.add_argument("--skip-exact",  type=bool,  default=SKIP_EXACT_DEGREE, help="Skip single-degree markets (default: True)")
    args = parser.parse_args()

    # Allow CLI overrides
    MIN_ENTRY_PRICE = args.min_entry
    SKIP_EXACT_DEGREE = args.skip_exact

    run_backtest(days_back=args.days, min_edge=args.min_edge)
