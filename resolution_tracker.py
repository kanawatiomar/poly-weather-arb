from discord_alert import post_discord
"""
resolution_tracker.py — Model calibration tracker for Polymarket weather arb.

For each trade in trades_db.jsonl:
  - Checks if the market has resolved (price >= 0.99 or <= 0.01)
  - Records outcome (WIN/LOSS), final price, P&L
  - Computes calibration stats: does our model's edge actually predict wins?

Run daily (or after markets resolve). Posts summary to Discord.

Calibration insight: if our model says 40% edge and we're winning 50% of those,
the model is well-calibrated. If winning 20%, the market was smarter than us.
"""

import json, httpx, time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE = Path(__file__).parent
TRADES_DB     = BASE / "trades_db.jsonl"
DISCORD_CHANNEL = "1479364504943857684"
RESOLVE_THRESHOLD = 0.97   # price above this = WIN
LOSS_THRESHOLD    = 0.03   # price below this = LOSS

def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def post_discord(msg, token):
    if not token: return
    try:
        httpx.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"content": msg[:1990]}, timeout=8,
        )
    except: pass

def get_price(token_id):
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

def load_trades():
    if not TRADES_DB.exists():
        return []
    trades = []
    for line in TRADES_DB.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                trades.append(json.loads(line))
            except: pass
    return trades

def save_trades(trades):
    with open(TRADES_DB, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")

def edge_bucket(edge_pct):
    e = abs(edge_pct or 0)
    if e < 0.10: return "<10%"
    if e < 0.20: return "10-20%"
    if e < 0.30: return "20-30%"
    if e < 0.40: return "30-40%"
    if e < 0.50: return "40-50%"
    return "50%+"

def calibration_report(trades):
    resolved = [t for t in trades if t.get("resolved")]
    if not resolved:
        return None, None

    # By edge bucket
    buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0,
                                    "avg_model_prob": [], "avg_edge": []})
    total_pnl = 0.0
    wins = 0

    for t in resolved:
        b = edge_bucket(t.get("edge_pct", 0))
        outcome = t.get("outcome")
        pnl = t.get("pnl", 0) or 0
        buckets[b]["trades"] += 1
        buckets[b]["pnl"] += pnl
        total_pnl += pnl
        if outcome == "WIN":
            buckets[b]["wins"] += 1
            wins += 1
        mp = t.get("model_prob")
        if mp: buckets[b]["avg_model_prob"].append(mp)
        ep = t.get("edge_pct")
        if ep: buckets[b]["avg_edge"].append(abs(ep))

    lines = ["**[CALIBRATION] Model Calibration Report**\n"]
    lines.append(f"Resolved trades: **{len(resolved)}** | "
                 f"Win rate: **{wins/len(resolved):.0%}** | "
                 f"Total P&L: **{'+'if total_pnl>=0 else ''}${total_pnl:.2f}**\n")
    lines.append("```")
    lines.append(f"{'Edge Bucket':<12} {'Trades':>6} {'Win%':>6} {'Model%':>8} {'P&L':>8}")
    lines.append("-" * 44)

    for bucket_name in ["<10%","10-20%","20-30%","30-40%","40-50%","50%+"]:
        b = buckets.get(bucket_name)
        if not b or b["trades"] == 0: continue
        win_rate  = b["wins"] / b["trades"]
        avg_model = sum(b["avg_model_prob"]) / len(b["avg_model_prob"]) if b["avg_model_prob"] else 0
        pnl_str   = f"{'+'if b['pnl']>=0 else ''}${b['pnl']:.2f}"
        # calibration flag: if win_rate much lower than avg_model, we're miscalibrated
        flag = " ⚠️" if avg_model > 0 and win_rate < avg_model * 0.6 else ""
        lines.append(f"{bucket_name:<12} {b['trades']:>6} {win_rate:>5.0%} {avg_model:>7.0%} {pnl_str:>8}{flag}")

    lines.append("```")

    # Key insight
    if len(resolved) >= 5:
        overall_win_rate = wins / len(resolved)
        avg_model_prob   = sum(t.get("model_prob") or 0.5 for t in resolved) / len(resolved)
        if overall_win_rate < avg_model_prob * 0.6:
            lines.append(f"\n⚠️ **MISCALIBRATION SIGNAL**: Win rate {overall_win_rate:.0%} vs model avg {avg_model_prob:.0%} — market knows more than our model in these markets")
        elif overall_win_rate >= avg_model_prob * 0.85:
            lines.append(f"\n✅ **WELL CALIBRATED**: Win rate {overall_win_rate:.0%} matches model avg {avg_model_prob:.0%}")
        else:
            lines.append(f"\n🟡 **MIXED**: Win rate {overall_win_rate:.0%} vs model avg {avg_model_prob:.0%} — collecting more data")

    return "\n".join(lines), total_pnl

def resolve_paper_trades():
    """Check and update paper trade outcomes."""
    paper_db = BASE / "paper_trades.jsonl"
    if not paper_db.exists():
        return 0
    trades = []
    for line in paper_db.read_text().splitlines():
        if line.strip():
            try: trades.append(json.loads(line))
            except: pass

    updated = 0
    for t in trades:
        if t.get("resolved"): continue
        cur = get_price(t.get("token_id",""))
        if cur is None: continue
        if cur >= RESOLVE_THRESHOLD:
            t["resolved"] = True; t["outcome"] = "WIN"
            t["final_price"] = cur; t["pnl"] = (cur - t["entry_price"]) * t.get("paper_size",1); updated += 1
        elif cur <= LOSS_THRESHOLD:
            t["resolved"] = True; t["outcome"] = "LOSS"
            t["final_price"] = cur; t["pnl"] = (cur - t["entry_price"]) * t.get("paper_size",1); updated += 1

    with open(paper_db, "w") as f:
        for t in trades: f.write(json.dumps(t) + "\n")
    return updated

def main():
    env   = load_env()
    token = env.get("DISCORD_BOT_TOKEN","")

    # Resolve paper trades silently
    paper_resolved = resolve_paper_trades()
    if paper_resolved:
        print(f"  Resolved {paper_resolved} paper trade(s)")

    trades = load_trades()
    if not trades:
        print("No trades in trades_db.jsonl yet.")
        return

    print(f"[ResolutionTracker] {datetime.now().strftime('%H:%M:%S')} -- {len(trades)} trades total")
    unresolved = [t for t in trades if not t.get("resolved")]
    print(f"  {len(unresolved)} unresolved, {len(trades)-len(unresolved)} already resolved")

    new_resolutions = []

    for i, trade in enumerate(trades):
        if trade.get("resolved"):
            continue

        token_id    = trade.get("token_id","")
        entry_price = float(trade.get("entry_price", 0))
        size        = float(trade.get("size", 0))
        signal      = trade.get("signal","YES")
        question    = trade.get("question","")[:60]

        if not token_id or size == 0:
            continue

        cur_price = get_price(token_id)
        if cur_price is None:
            print(f"  [?] {question} — price unavailable")
            continue

        print(f"  {question} | entry={entry_price:.3f} now={cur_price:.3f}")

        if cur_price >= RESOLVE_THRESHOLD:
            outcome = "WIN"
            pnl     = (cur_price - entry_price) * size
        elif cur_price <= LOSS_THRESHOLD:
            outcome = "LOSS"
            pnl     = (cur_price - entry_price) * size
        else:
            print(f"    -> Still open ({cur_price:.3f})")
            continue

        trade["resolved"]    = True
        trade["resolved_at"] = datetime.utcnow().isoformat()
        trade["outcome"]     = outcome
        trade["final_price"] = cur_price
        trade["pnl"]         = round(pnl, 4)

        emoji = "✅" if outcome == "WIN" else "❌"
        model_prob = trade.get("model_prob", 0)
        forecast   = trade.get("forecast_mean")
        edge       = trade.get("edge_pct", 0)

        print(f"    -> {emoji} {outcome}  P&L: {'+'if pnl>=0 else ''}${pnl:.2f}")
        new_resolutions.append(
            f"{emoji} **{outcome}** | {question}\n"
            f"  Entry {entry_price:.3f} → {cur_price:.3f} | P&L: **{'+'if pnl>=0 else ''}${pnl:.2f}** | "
            f"Model: {model_prob:.0%} prob, edge was {edge:+.0%}"
            + (f", forecast {forecast:.1f}°" if forecast else "")
        )

        time.sleep(0.3)

    save_trades(trades)

    # Post new resolutions
    if new_resolutions:
        msg = "**🏁 New Market Resolutions**\n\n" + "\n\n".join(new_resolutions)
        post_discord(msg, token)

    # Always print calibration; post if ≥5 resolved
    report, total_pnl = calibration_report(trades)
    if report:
        print(f"\n{report}")
        resolved_count = sum(1 for t in trades if t.get("resolved"))
        if resolved_count >= 5:
            post_discord(report, token)
    else:
        print("  Not enough resolved trades for calibration report yet.")

    print(f"\n[ResolutionTracker] Done. {len(new_resolutions)} new resolutions.")

if __name__ == "__main__":
    main()

