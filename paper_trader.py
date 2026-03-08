"""
paper_trader.py -- Simulated trading for strategy validation.

Runs same logic as auto_trade.py but records to paper_trades.jsonl instead
of placing real orders. Lower edge threshold to gather more data points.

Key differences from real trading:
- MIN_EDGE lowered to 10% (vs 20% real) -- more data points
- No concentration limits -- test all markets
- No capital constraints -- size every trade
- Tracks outcomes via resolution_tracker logic

Run after every scanner run to build calibration dataset fast.
"""

import json, httpx, sys
from pathlib import Path
from datetime import datetime, date

BASE = Path(__file__).parent
PAPER_DB = BASE / "paper_trades.jsonl"

def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def post_paper_discord(msg):
    env = load_env()
    url = env.get("PAPER_DISCORD_WEBHOOK","")
    if not url: return
    try:
        httpx.post(url, json={"content": msg[:1990]}, timeout=8)
    except: pass

MIN_EDGE      = 0.10   # lower than real (0.20) to get more data
MAX_TRADES    = 20     # paper trade more markets per scan
PAPER_BANKROLL = 1000  # hypothetical bankroll for sizing
KELLY_FRAC    = 0.5
MIN_BET       = 1.0
MAX_BET       = 50.0   # higher cap — sizing is hypothetical
MIN_DATE      = (date.today()).isoformat()  # include today for paper

def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

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
    bet = PAPER_BANKROLL * KELLY_FRAC * kelly_pct * tail_penalty
    return round(max(MIN_BET, min(MAX_BET, bet)), 2)

def get_current_price(token_id):
    try:
        r = httpx.get(f"https://clob.polymarket.com/last-trade-price?token_id={token_id}", timeout=6)
        p = float(r.json().get("price", 0))
        if p > 0: return p
    except: pass
    try:
        r = httpx.get(f"https://clob.polymarket.com/midpoint?token_id={token_id}", timeout=6)
        p = float(r.json().get("mid", 0))
        if p > 0: return p
    except: pass
    return None

def load_existing_paper_trades():
    """Return set of (token_id, date) already paper traded to avoid duplicates."""
    existing = set()
    if PAPER_DB.exists():
        for line in PAPER_DB.read_text().splitlines():
            if line.strip():
                try:
                    t = json.loads(line)
                    existing.add((t["token_id"], t["market_date"]))
                except: pass
    return existing

def main():
    results_file = BASE / "scan_results.json"
    if not results_file.exists():
        print("No scan_results.json — run scanner.py first")
        sys.exit(0)

    with open(results_file) as f:
        results = json.load(f)

    opps = [
        o for o in results.get("opportunities", [])
        if abs(o.get("edge_pct", 0)) >= MIN_EDGE
        and o.get("date", "") >= MIN_DATE
    ]
    opps.sort(key=lambda x: abs(x.get("edge_pct", 0)), reverse=True)
    top = opps[:MAX_TRADES]

    existing = load_existing_paper_trades()
    scan_date = results.get("date", datetime.utcnow().date().isoformat())

    print(f"[PaperTrader] {datetime.now().strftime('%H:%M:%S')} -- {len(opps)} opps, paper trading top {MAX_TRADES}")

    new_trades = 0
    with open(PAPER_DB, "a") as f:
        for opp in top:
            token_id  = opp["token_id"]
            mdate     = opp.get("date", "")
            signal    = opp.get("signal", "YES")
            edge      = opp.get("edge_pct", 0)
            yes_price = float(opp.get("yes_price", 0.5))
            question  = opp.get("question", "")

            # Skip duplicates
            if (token_id, mdate) in existing:
                continue

            price = round(yes_price, 4) if signal == "YES" else round(1.0 - yes_price, 4)
            price = max(0.01, min(0.99, price))

            cur_price = get_current_price(token_id)

            f_mean = opp.get("forecast_mean")
            f_low  = opp.get("range_low")
            f_high = opp.get("range_high")
            bet    = kelly_size(abs(edge), price, f_mean, f_low, f_high)
            size   = round(bet / price, 1)

            record = {
                "type"         : "paper",
                "placed_at"    : datetime.utcnow().isoformat(),
                "scan_date"    : scan_date,
                "question"     : question,
                "city"         : opp.get("city", ""),
                "market_date"  : mdate,
                "signal"       : signal,
                "token_id"     : token_id,
                "entry_price"  : price,
                "model_prob"   : opp.get("model_prob"),
                "forecast_mean": f_mean,
                "forecast_std" : opp.get("forecast_std"),
                "models_used"  : opp.get("models_used"),
                "edge_pct"     : edge,
                "range_low"    : f_low,
                "range_high"   : f_high,
                "tail_bet"     : f_mean is not None and f_low is not None and f_high is not None
                                 and not (f_low <= f_mean <= f_high),
                "paper_bet"    : bet,
                "paper_size"   : size,
                "current_price": cur_price,
                "resolved"     : False,
                "outcome"      : None,
                "final_price"  : None,
                "pnl"          : None,
            }

            f.write(json.dumps(record) + "\n")
            existing.add((token_id, mdate))
            new_trades += 1

            tail_str = " [TAIL BET]" if record["tail_bet"] else ""
            print(f"  [{signal}] {question[:55]} | edge {edge:+.0%} | ${bet:.2f}{tail_str}")

    print(f"[PaperTrader] {new_trades} new paper trades logged")

    # Post new trades to #paper-trading
    if new_trades > 0:
        lines = [f"**📝 Paper Trades Logged — {datetime.now().strftime('%I:%M %p').lstrip('0')} | {new_trades} new**\n"]
        # re-read last N trades
        all_trades = []
        if PAPER_DB.exists():
            for line in PAPER_DB.read_text().splitlines()[-new_trades:]:
                if line.strip():
                    try: all_trades.append(json.loads(line))
                    except: pass
        for t in all_trades:
            tail = " ⚡tail" if t.get("tail_bet") else ""
            lines.append(f"  [{t['signal']}] {t['question'][:50]} | edge {t['edge_pct']:+.0%}{tail}")
        post_paper_discord("\n".join(lines))

    # Quick calibration on resolved paper trades
    resolved = []
    if PAPER_DB.exists():
        for line in PAPER_DB.read_text().splitlines():
            if line.strip():
                try:
                    t = json.loads(line)
                    if t.get("resolved"):
                        resolved.append(t)
                except: pass

    if resolved:
        wins = sum(1 for t in resolved if t.get("outcome") == "WIN")
        total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved)
        tail_resolved = [t for t in resolved if t.get("tail_bet")]
        non_tail      = [t for t in resolved if not t.get("tail_bet")]
        win_rate = wins/len(resolved)

        print(f"\n  Paper calibration ({len(resolved)} resolved):")
        print(f"  Win rate: {wins}/{len(resolved)} = {win_rate:.0%} | P&L: ${total_pnl:+.2f}")

        cal_lines = [f"**📊 Paper Calibration — {len(resolved)} resolved trades**\n"]
        cal_lines.append(f"Win rate: **{wins}/{len(resolved)} ({win_rate:.0%})** | Paper P&L: **{'+'if total_pnl>=0 else ''}${total_pnl:.2f}**\n")

        if tail_resolved:
            tw = sum(1 for t in tail_resolved if t.get("outcome")=="WIN")
            print(f"  Tail bets: {tw}/{len(tail_resolved)} wins ({tw/len(tail_resolved):.0%})")
            cal_lines.append(f"⚡ Tail bets (mean outside range): {tw}/{len(tail_resolved)} wins ({tw/len(tail_resolved):.0%})")
        if non_tail:
            nw = sum(1 for t in non_tail if t.get("outcome")=="WIN")
            print(f"  In-range bets: {nw}/{len(non_tail)} wins ({nw/len(non_tail):.0%})")
            cal_lines.append(f"✅ In-range bets (mean inside range): {nw}/{len(non_tail)} wins ({nw/len(non_tail):.0%})")

        if len(resolved) >= 5:
            post_paper_discord("\n".join(cal_lines))

if __name__ == "__main__":
    main()
