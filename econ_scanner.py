"""
econ_scanner.py -- Polymarket economic data scanner
Compares model-implied probabilities to Polymarket market prices.
Sources: CME FedWatch (Fed), Cleveland Fed (CPI), BLS/ADP (NFP)
Posts opportunities + paper trades to #econ-scanner Discord channel.
"""

import httpx, json, re, math
from datetime import datetime, date, timezone
from pathlib import Path
from scipy import stats

BASE = Path(__file__).parent
PAPER_DB = BASE / "econ_paper_trades.jsonl"
RESULTS_FILE = BASE / "econ_scan_results.json"

MIN_EDGE = 0.08        # 8% min edge for paper trades
MAX_PAPER_BET = 50     # paper bankroll bet cap
PAPER_BANKROLL = 1000  # hypothetical paper bankroll

# ── ENV ───────────────────────────────────────────────────────────
def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def post_discord(msg, webhook_key="ECON_DISCORD_WEBHOOK"):
    env = load_env()
    url = env.get(webhook_key, "")
    if not url:
        return
    try:
        httpx.post(url, json={"content": msg[:1990]}, timeout=8)
    except Exception as e:
        print(f"Discord error: {e}")


# ── POLYMARKET: FIND ECON MARKETS ────────────────────────────────
ECON_KEYWORDS = [
    "fed rate", "rate cut", "rate hike", "fomc", "federal reserve",
    "cpi", "inflation", "consumer price",
    "non-farm", "nonfarm", "payroll", "jobs report",
    "unemployment", "jobless",
    "gdp", "gross domestic",
    "ppi", "producer price",
    "retail sales", "core pce"
]

def fetch_econ_markets():
    """Fetch all active Polymarket markets matching economic data keywords."""
    markets = []
    seen = set()

    for tag in ["finance", "politics"]:
        try:
            r = httpx.get("https://gamma-api.polymarket.com/events", params={
                "active": "true", "closed": "false", "limit": 200, "tag_slug": tag
            }, timeout=15)
            events = r.json() or []
        except:
            events = []

        for event in events:
            title = event.get("title", "").lower()
            if not any(k in title for k in ECON_KEYWORDS):
                continue
            for m in event.get("markets", []):
                mid = m.get("id") or m.get("conditionId", "")
                if mid in seen:
                    continue
                seen.add(mid)
                # Fetch full market data to get outcomePrices
                try:
                    mr = httpx.get(f"https://gamma-api.polymarket.com/markets/{mid}", timeout=8)
                    full = mr.json() if mr.is_success else {}
                except:
                    full = {}
                markets.append({
                    "id": mid,
                    "question": m.get("question", ""),
                    "event_title": event.get("title", ""),
                    "end_date": m.get("endDate") or event.get("endDate", ""),
                    "tokens": m.get("tokens", []),
                    "slug": m.get("slug",""),
                    "clob_token_ids": m.get("clobTokenIds", []),
                    "outcomePrices": full.get("outcomePrices", m.get("outcomePrices")),
                    "volume": full.get("volume", 0),
                })

    print(f"[EconScanner] Found {len(markets)} econ markets on Polymarket")
    return markets


def get_market_price(market):
    """
    Get YES token price for a market.
    Tries: gamma outcomePrices → CLOB last trade → CLOB midpoint.
    """
    # 1) gamma API outcomePrices (most reliable for AMM markets)
    op = market.get("outcomePrices")
    if op:
        try:
            prices = json.loads(op) if isinstance(op, str) else op
            if prices and len(prices) >= 1:
                p = float(prices[0])
                if 0.01 < p < 0.99:
                    return p
        except:
            pass

    # 2) tokens field price
    for tok in market.get("tokens", []):
        if tok.get("outcome", "").upper() == "YES":
            p = tok.get("price")
            if p is not None:
                try:
                    p = float(p)
                    if 0.01 < p < 0.99:
                        return p
                except:
                    pass

    # 3) CLOB last trade price
    clob_ids = market.get("clob_token_ids", [])
    yes_token = clob_ids[0] if clob_ids else None
    if yes_token:
        try:
            r = httpx.get(f"https://clob.polymarket.com/last-trade-price?token_id={yes_token}", timeout=6)
            if r.is_success:
                p = float(r.json().get("price", 0))
                # Last trade price might be YES or NO side — use only if reasonable
                if 0.02 < p < 0.98:
                    return p
        except:
            pass

        # 4) CLOB midpoint (only if spread < 0.30 — else illiquid AMM)
        try:
            r = httpx.get(f"https://clob.polymarket.com/book?token_id={yes_token}", timeout=6)
            if r.is_success:
                data = r.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 1))
                    spread = best_ask - best_bid
                    if spread < 0.30:
                        return (best_bid + best_ask) / 2
        except:
            pass

    return None


# ── FORECAST MODELS ───────────────────────────────────────────────

def get_fedwatch_probabilities():
    """
    Fetch Fed meeting cut probabilities from CME FedWatch.
    Returns dict of meeting_date → {cut_prob, hold_prob, hike_prob}
    """
    probs = {}
    try:
        # CME provides this JSON endpoint
        r = httpx.get(
            "https://www.cmegroup.com/CmeWS/mvc/ProductMain/getEodSettlements.do"
            "?productId=8462&exchange=CME",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cmegroup.com/"},
            timeout=12
        )
        # Try the FedWatch API
        r2 = httpx.get(
            "https://www.cmegroup.com/CmeWS/mvc/Summary/eventCalendar.do",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12
        )
    except:
        pass

    # Fallback: Use hardcoded current FedWatch probabilities (update weekly)
    # Source: https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html
    # As of 2026-03-08: Fed funds rate 4.25-4.50%
    probs = {
        "2026-03-19": {"cut_25": 0.04, "hold": 0.95, "hike": 0.01},  # March FOMC
        "2026-05-07": {"cut_25": 0.18, "hold": 0.80, "hike": 0.02},  # May FOMC
        "2026-06-18": {"cut_25": 0.38, "hold": 0.60, "hike": 0.02},  # June FOMC
        "2026-07-30": {"cut_25": 0.52, "hold": 0.46, "hike": 0.02},  # July FOMC
        "2026-09-17": {"cut_25": 0.65, "hold": 0.33, "hike": 0.02},  # Sep FOMC
        "2026-11-05": {"cut_25": 0.72, "hold": 0.26, "hike": 0.02},  # Nov FOMC
        "2026-12-10": {"cut_25": 0.78, "hold": 0.20, "hike": 0.02},  # Dec FOMC
    }

    # Try to get live data from a scrapeable source
    try:
        r = httpx.get("https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=10, follow_redirects=True)
        # Parse embedded JSON data if present
        json_match = re.search(r'"probabilities":\s*(\{[^}]+\})', r.text)
        if json_match:
            live_probs = json.loads(json_match.group(1))
            probs.update(live_probs)
            print("[FedWatch] Got live probabilities")
        else:
            print("[FedWatch] Using cached probabilities (update weekly)")
    except Exception as e:
        print(f"[FedWatch] Fetch failed: {e}, using cached")

    return probs


def get_cpi_nowcast():
    """
    Cleveland Fed CPI nowcast — best free real-time CPI forecast.
    Returns (nowcast_yoy, sigma) for current month's CPI.
    """
    try:
        # Cleveland Fed Inflation Nowcasting page
        r = httpx.get(
            "https://www.clevelandfed.org/en/indicators-and-data/inflation-nowcasting.aspx",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=12, follow_redirects=True
        )
        # Look for the nowcast value in the page
        # Pattern: "Current Month CPI: 2.9%" or similar embedded data
        matches = re.findall(r'(\d+\.\d+)\s*%', r.text[:5000])
        if matches:
            nowcast = float(matches[0])
            print(f"[CPI] Cleveland Fed nowcast: {nowcast:.2f}%")
            return nowcast, 0.15  # historical 1-sigma error ~0.15%
    except Exception as e:
        print(f"[CPI] Nowcast fetch failed: {e}")

    # Fallback: use last known CPI + consensus
    # Jan 2026 CPI was ~3.0% YoY; consensus for Feb 2026 ~2.9%
    return 2.9, 0.15


def model_fed_cut_prob(meeting_date_str, question):
    """
    Return model probability that question resolves YES for Fed meeting.
    Uses FedWatch probabilities.
    """
    fed_probs = get_fedwatch_probabilities()

    q_lower = question.lower()

    # Detect meeting from question text
    matched_meeting = None
    for meeting_date, probs in fed_probs.items():
        # Check if meeting date or month appears in question
        dt = datetime.strptime(meeting_date, "%Y-%m-%d")
        month_name = dt.strftime("%B").lower()
        month_short = dt.strftime("%b").lower()
        if month_name in q_lower or month_short in q_lower:
            matched_meeting = (meeting_date, probs)
            break

    if not matched_meeting:
        # Use next upcoming meeting
        today = date.today()
        for meeting_date in sorted(fed_probs.keys()):
            if meeting_date >= today.isoformat():
                matched_meeting = (meeting_date, fed_probs[meeting_date])
                break

    if not matched_meeting:
        return None

    meeting_date, probs = matched_meeting

    # Determine what the question is asking
    if "cut" in q_lower and "pause" not in q_lower and "hike" not in q_lower:
        return probs.get("cut_25", 0.3)
    elif "hold" in q_lower or "pause" in q_lower or "unchanged" in q_lower:
        return probs.get("hold", 0.6)
    elif "hike" in q_lower or "raise" in q_lower or "increase" in q_lower:
        return probs.get("hike", 0.02)
    elif "no cut" in q_lower:
        return 1 - probs.get("cut_25", 0.3)

    return None


def model_cpi_prob(question):
    """
    Return model probability for CPI-related question.
    Uses Cleveland Fed nowcast + historical forecast error.
    """
    nowcast, sigma = get_cpi_nowcast()

    # Extract threshold from question
    threshold_match = re.search(r'(\d+\.?\d*)\s*%', question)
    if not threshold_match:
        return None
    threshold = float(threshold_match.group(1))

    q_lower = question.lower()
    # Determine direction
    if "above" in q_lower or "exceed" in q_lower or "over" in q_lower or "higher" in q_lower:
        prob = 1 - stats.norm.cdf(threshold, loc=nowcast, scale=sigma)
    elif "below" in q_lower or "under" in q_lower or "less" in q_lower or "lower" in q_lower:
        prob = stats.norm.cdf(threshold, loc=nowcast, scale=sigma)
    else:
        return None

    return float(prob)


def model_probability(question, event_title):
    """Route to appropriate model based on market type."""
    q = question.lower()
    e = event_title.lower()

    if any(k in q or k in e for k in ["fed rate", "rate cut", "fomc", "federal reserve"]):
        return model_fed_cut_prob(None, question), "fed"

    if any(k in q or k in e for k in ["cpi", "consumer price", "inflation"]):
        return model_cpi_prob(question), "cpi"

    return None, "unknown"


# ── OPPORTUNITY SCORING ───────────────────────────────────────────
def score_market(market):
    """Score a market against model. Returns opportunity dict or None."""
    question = market["question"]
    event_title = market["event_title"]

    # Get model probability
    model_prob, model_type = model_probability(question, event_title)
    if model_prob is None or model_prob <= 0 or model_prob >= 1:
        return None

    market_price = get_market_price(market)
    if market_price is None:
        return None

    # Edge calculation
    edge = model_prob - market_price
    edge_pct = edge / market_price if market_price > 0 else 0

    # Only return significant edges
    if abs(edge) < 0.05:
        return None

    signal = "BUY YES" if edge > 0 else "BUY NO"
    if signal == "BUY NO":
        market_price = 1 - market_price
        model_prob = 1 - model_prob
        edge = model_prob - market_price
        edge_pct = edge / market_price if market_price > 0 else 0

    return {
        "question": question[:80],
        "event": event_title[:50],
        "model_type": model_type,
        "model_prob": round(model_prob, 4),
        "market_price": round(market_price, 4),
        "edge": round(edge, 4),
        "edge_pct": round(edge_pct, 4),
        "signal": signal,
        "market_id": market.get("id",""),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


# ── KELLY SIZING ──────────────────────────────────────────────────
def kelly_size(model_prob, market_price, bankroll=PAPER_BANKROLL, max_bet=MAX_PAPER_BET):
    if market_price <= 0 or market_price >= 1:
        return 0
    odds = (1 - market_price) / market_price
    kelly = (model_prob * odds - (1 - model_prob)) / odds
    kelly = max(0, kelly * 0.5)  # half-Kelly
    return min(round(kelly * bankroll, 2), max_bet)


# ── PAPER TRADING ─────────────────────────────────────────────────
def paper_trade(opportunities):
    if not opportunities:
        return 0

    existing = set()
    if PAPER_DB.exists():
        for line in PAPER_DB.read_text().splitlines():
            if line.strip():
                try:
                    t = json.loads(line)
                    existing.add(t.get("market_id","") + t.get("signal",""))
                except:
                    pass

    new_trades = 0
    with open(PAPER_DB, "a") as f:
        for opp in opportunities:
            if abs(opp["edge_pct"]) < MIN_EDGE:
                continue
            key = opp.get("market_id","") + opp.get("signal","")
            if key in existing:
                continue

            bet = kelly_size(opp["model_prob"], opp["market_price"])
            if bet < 0.50:
                continue

            trade = {**opp, "paper_bet": bet, "status": "open",
                     "logged_at": datetime.now(timezone.utc).isoformat()}
            f.write(json.dumps(trade) + "\n")
            existing.add(key)
            new_trades += 1

    return new_trades


# ── DISCORD POSTING ───────────────────────────────────────────────
def post_scan_results(opportunities, new_trades):
    if not opportunities and not new_trades:
        print("[EconScanner] No opportunities found.")
        return

    ts = datetime.now().strftime("%I:%M %p").lstrip("0")
    lines = [f"**📊 Econ Scanner — {ts} | {len(opportunities)} edges found | {new_trades} new paper trades**\n"]

    for opp in sorted(opportunities, key=lambda x: -abs(x["edge_pct"]))[:10]:
        direction = "🟢 BUY" if opp["edge"] > 0 else "🔴 SELL"
        lines.append(
            f"{direction} | **{opp['model_type'].upper()}** | {opp['question'][:60]}\n"
            f"  Model: **{opp['model_prob']:.0%}** vs Market: **{opp['market_price']:.0%}** | Edge: **{opp['edge_pct']:+.0%}**"
        )

    post_discord("\n".join(lines))


# ── MAIN ──────────────────────────────────────────────────────────
def main():
    print(f"[EconScanner] {datetime.now().strftime('%H:%M:%S')} — Starting scan...")

    markets = fetch_econ_markets()
    if not markets:
        print("[EconScanner] No markets found. Exiting.")
        return

    opportunities = []
    for market in markets:
        opp = score_market(market)
        if opp:
            opportunities.append(opp)
            print(f"  EDGE: {opp['edge_pct']:+.0%} | {opp['signal']} | {opp['question'][:60]}")

    print(f"[EconScanner] {len(opportunities)} opportunities found")

    # Paper trade
    new_trades = paper_trade(opportunities)
    print(f"[EconScanner] {new_trades} new paper trades logged")

    # Save results
    RESULTS_FILE.write_text(json.dumps({
        "scanned_at": datetime.now().isoformat(),
        "opportunities": opportunities
    }, indent=2))

    # Post to Discord
    post_scan_results(opportunities, new_trades)

    # Post model notes
    if not opportunities:
        post_discord(
            f"**📊 Econ Scanner — {datetime.now().strftime('%I:%M %p').lstrip('0')}**\n"
            f"Scanned {len(markets)} markets · No edges above threshold today.\n"
            f"Models: FedWatch (Fed), Cleveland Fed CPI Nowcast"
        )


if __name__ == "__main__":
    main()
