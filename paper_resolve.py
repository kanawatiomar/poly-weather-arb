# -*- coding: utf-8 -*-
"""
paper_resolve.py - Resolve paper trades and post calibration stats.

For each unresolved paper trade in paper_trades.jsonl:
  - Checks current CLOB price for the token
  - If price >= 0.97 -> WIN (resolved in our favor)
  - If price <= 0.03 -> LOSS (resolved against us)
  - Updates paper_trades.jsonl with outcome + final P&L
  - Posts calibration summary to #paper-trading Discord channel

Run after scanner + paper_trader (e.g., 8 AM and 6 PM daily).
"""

import json, httpx, time
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

BASE        = Path(__file__).parent
PAPER_DB    = BASE / "paper_trades.jsonl"

# Discord channel for paper trading
PAPER_CHANNEL = "1480301606112329935"

RESOLVE_WIN  = 0.97   # price above this = WIN
RESOLVE_LOSS = 0.03   # price below this = LOSS
POLY_FEE     = 0.02


def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def post_discord(msg, token):
    if not token:
        return
    try:
        httpx.post(
            f"https://discord.com/api/v10/channels/{PAPER_CHANNEL}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"content": msg[:1990]},
            timeout=8,
        )
    except Exception:
        pass


def get_price(token_id):
    """Fetch current CLOB price for a token."""
    try:
        r = httpx.get(
            f"https://clob.polymarket.com/last-trade-price?token_id={token_id}",
            timeout=6,
        )
        p = float(r.json().get("price", 0))
        if p > 0:
            return p
    except Exception:
        pass
    try:
        r = httpx.get(
            f"https://clob.polymarket.com/midpoint?token_id={token_id}",
            timeout=6,
        )
        p = float(r.json().get("mid", 0))
        if p > 0:
            return p
    except Exception:
        pass
    return None


def load_trades():
    if not PAPER_DB.exists():
        return []
    trades = []
    for line in PAPER_DB.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                trades.append(json.loads(line))
            except Exception:
                pass
    return trades


def save_trades(trades):
    with open(PAPER_DB, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


def calc_pnl(trade, signal_price, final_price):
    """Calculate P&L for a paper trade given final price."""
    bet        = trade.get("paper_bet", 0)
    signal     = trade.get("signal", "YES")
    entry      = trade.get("entry_price", signal_price)

    if signal == "YES":
        # Bought YES at entry_price, resolves to final_price
        if final_price >= RESOLVE_WIN:
            # WIN: profit = bet * (1 - entry) * (1 - fee)
            return bet * (1 - entry) * (1 - POLY_FEE)
        else:
            # LOSS: lose bet
            return -bet
    else:
        # Bought NO at entry_price (= 1 - yes_price_at_entry)
        if final_price <= RESOLVE_LOSS:
            # YES went to 0, NO = 1 -> WIN
            return bet * (1 - entry) * (1 - POLY_FEE)
        else:
            # YES stayed up, NO = 0 -> LOSS
            return -bet


def edge_bucket(edge_pct):
    e = abs(edge_pct or 0)
    if e < 0.10: return "<10%"
    if e < 0.20: return "10-20%"
    if e < 0.30: return "20-30%"
    if e < 0.40: return "30-40%"
    if e < 0.50: return "40-50%"
    return "50%+"


def main():
    env   = load_env()
    token = env.get("DISCORD_BOT_TOKEN", "")

    trades = load_trades()
    if not trades:
        print("[PaperResolve] No trades found in paper_trades.jsonl")
        return

    unresolved = [t for t in trades if not t.get("resolved")]
    print(f"[PaperResolve] {datetime.now().strftime('%H:%M:%S')} | "
          f"{len(unresolved)} unresolved / {len(trades)} total")

    newly_resolved = 0
    for t in trades:
        if t.get("resolved"):
            continue

        token_id = t.get("token_id")
        signal   = t.get("signal", "YES")
        entry    = t.get("entry_price", 0.5)

        if not token_id:
            continue

        # For YES signal: check YES token price
        # For NO signal: check YES token price (NO wins when YES goes to 0)
        price = get_price(token_id)
        time.sleep(0.1)

        if price is None:
            continue

        t["current_price"] = price

        # Determine resolution
        if signal == "YES":
            if price >= RESOLVE_WIN:
                outcome = "WIN"
            elif price <= RESOLVE_LOSS:
                outcome = "LOSS"
            else:
                continue  # still open
        else:
            # NO signal: win when YES token goes to 0
            if price <= RESOLVE_LOSS:
                outcome = "WIN"
            elif price >= RESOLVE_WIN:
                outcome = "LOSS"
            else:
                continue  # still open

        pnl = calc_pnl(t, entry, price)

        t["resolved"]    = True
        t["outcome"]     = outcome
        t["final_price"] = price
        t["pnl"]         = round(pnl, 4)
        t["resolved_at"] = datetime.utcnow().isoformat()
        newly_resolved  += 1

        icon = "WIN" if outcome == "WIN" else "LOSS"
        print(f"  [{icon}] {t.get('question','')[:55]} | "
              f"signal={signal} entry={entry:.3f} final={price:.3f} "
              f"P&L=${pnl:+.2f}")

    if newly_resolved > 0:
        save_trades(trades)
        print(f"[PaperResolve] Resolved {newly_resolved} new trades")

    # ── Calibration summary ───────────────────────────────────────────────────
    resolved_trades = [t for t in trades if t.get("resolved")]
    if not resolved_trades:
        print("[PaperResolve] No resolved trades yet for calibration")
        return

    wins      = sum(1 for t in resolved_trades if t.get("outcome") == "WIN")
    total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved_trades)
    win_rate  = wins / len(resolved_trades)

    # By market type
    above_below = [t for t in resolved_trades
                   if t.get("range_low") is None or t.get("range_high") is None]
    range_trades = [t for t in resolved_trades
                    if t.get("range_low") is not None and t.get("range_high") is not None]

    # By edge bucket
    buckets = defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0.0})
    for t in resolved_trades:
        b = edge_bucket(t.get("edge_pct", 0))
        buckets[b]["total"] += 1
        buckets[b]["pnl"]   += t.get("pnl", 0) or 0
        if t.get("outcome") == "WIN":
            buckets[b]["wins"] += 1

    print(f"\n[PaperResolve] Calibration ({len(resolved_trades)} resolved):")
    print(f"  Win rate: {wins}/{len(resolved_trades)} = {win_rate:.0%} | "
          f"P&L: ${total_pnl:+.2f}")

    # Only post to Discord when we have enough resolved trades to be meaningful
    if newly_resolved > 0 or len(resolved_trades) >= 5:
        lines = [
            f"**Paper Calibration Update — {date.today().strftime('%b %d')}**",
            f"Resolved: **{len(resolved_trades)}** trades | "
            f"Win rate: **{wins}/{len(resolved_trades)} ({win_rate:.0%})** | "
            f"Paper P&L: **{'+'if total_pnl>=0 else ''}${total_pnl:.2f}**",
            "",
        ]

        if len(above_below) > 0:
            ab_wins = sum(1 for t in above_below if t.get("outcome") == "WIN")
            ab_pnl  = sum(t.get("pnl", 0) or 0 for t in above_below)
            lines.append(f"Above/below markets: {ab_wins}/{len(above_below)} "
                         f"({ab_wins/len(above_below):.0%}) | ${ab_pnl:+.2f}")

        if len(range_trades) > 0:
            r_wins = sum(1 for t in range_trades if t.get("outcome") == "WIN")
            r_pnl  = sum(t.get("pnl", 0) or 0 for t in range_trades)
            lines.append(f"Range markets: {r_wins}/{len(range_trades)} "
                         f"({r_wins/len(range_trades):.0%}) | ${r_pnl:+.2f}")

        lines.append("")
        lines.append("**By edge bucket:**")
        for bname in ["10-20%", "20-30%", "30-40%", "40-50%", "50%+"]:
            b = buckets.get(bname)
            if b and b["total"] > 0:
                bwr = b["wins"] / b["total"]
                lines.append(f"  Edge {bname}: {b['wins']}/{b['total']} "
                             f"({bwr:.0%}) | ${b['pnl']:+.2f}")

        msg = "\n".join(lines)
        post_discord(msg, token)
        print("[PaperResolve] Posted calibration update to Discord")


if __name__ == "__main__":
    main()
