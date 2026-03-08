"""
position_monitor.py -- Position summary + alerts posted to Discord every run.
Posts a full Winners/Losers card every 15 min + fires instant alerts for:
  - Resolution (win/loss)
  - 2x runner
  - 60% bleed
  - 20% move since last check
"""
import json, httpx, time, re
from pathlib import Path
from datetime import datetime
from discord_alert import post_discord

BASE       = Path(__file__).parent
STATE_FILE = BASE / "monitor_state.json"

MOVE_ALERT_PCT  = 0.20
WINNER_MULT     = 2.0
BLEED_ALERT_PCT = 0.60

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"positions": {}}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

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

def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def fetch_wallet_positions():
    env = load_env()
    wallet = env.get("POLY_ADDRESS", "")
    try:
        r = httpx.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet, "sizeThreshold": "0.01"}, timeout=10
        )
        if r.is_success:
            return r.json()
    except: pass
    return []

def friendly_name(title, outcome):
    """Shorten market title to a readable label."""
    t = title or ""
    # UFC
    m = re.search(r'UFC \d+: (.+?) vs\.? (.+?) \(', t)
    if m:
        f1 = m.group(1).split()[-1]
        f2 = m.group(2).split()[-1]
        return f"UFC {f1} vs {f2}"
    # City temp
    for city in ["Buenos Aires","Wellington","Seattle","Miami","Toronto","Chicago",
                 "Dallas","Atlanta","New York","NYC","London","Seoul","Paris"]:
        if city.lower() in t.lower():
            out = outcome or "Yes"
            label = city
            if "main" in t.lower() or "23" in t:
                label += " (main)" if "23" in t else ""
            return f"{label} {out}"
    return t[:35]

def status_emoji(pnl, cur_price):
    if cur_price >= 0.97: return "✅"
    if cur_price <= 0.03: return "❌"
    if pnl >= 0: return "📈"
    return "⚠️"

def build_summary(positions_data, now_str, alerts_count):
    winners = []
    losers  = []
    net     = 0.0

    for p in positions_data:
        pnl       = p["pnl"]
        label     = p["label"]
        cur       = p["cur_price"]
        avg       = p["avg_price"]
        note      = p.get("note","")
        net      += pnl
        emoji     = status_emoji(pnl, cur)
        pnl_str   = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        line      = f"  {label} — **{pnl_str}** {emoji}"
        if note:
            line += f" ({note})"
        if pnl >= 0:
            winners.append(line)
        else:
            losers.append(line)

    lines = [f"**📊 Position Monitor — {now_str} | {len(positions_data)} positions**\n"]

    if winners:
        lines.append("🟢 **Winners**")
        lines.extend(winners)

    if losers:
        lines.append("\n🔴 **Losers**")
        lines.extend(losers)

    net_str = f"+${net:.2f}" if net >= 0 else f"-${abs(net):.2f}"
    alert_str = f"{alerts_count} alert{'s' if alerts_count != 1 else ''} sent" if alerts_count else "no alerts"
    lines.append(f"\n**Net: ~{net_str}** | {alert_str}")

    return "\n".join(lines)

def main():
    state = load_state()
    positions_state = state.get("positions", {})
    alerts = []
    positions_data = []

    positions = fetch_wallet_positions()
    now_str = datetime.now().strftime("%I:%M %p").lstrip("0")

    print(f"[Monitor] {datetime.now().strftime('%H:%M:%S')} -- {len(positions)} positions")

    for pos in positions:
        token_id  = pos.get("asset") or pos.get("tokenId", "")
        size      = float(pos.get("size", 0))
        avg_price = float(pos.get("avgPrice", 0) or pos.get("averagePrice", 0) or 0)
        title     = pos.get("title") or pos.get("question") or ""
        outcome   = pos.get("outcome", "Yes")

        if size < 0.5 or not token_id:
            continue

        cur_price = get_price(token_id)
        if cur_price is None:
            continue

        last_price = positions_state.get(token_id, {}).get("last_price")
        pnl        = (cur_price - avg_price) * size
        pnl_pct    = (cur_price - avg_price) / avg_price if avg_price > 0 else 0
        label      = friendly_name(title, outcome)

        print(f"  {label}: entry={avg_price:.3f} now={cur_price:.3f} pnl={pnl:+.2f}")

        # Note for special states
        note = ""
        if cur_price >= 0.97:
            note = "resolved ✓"
        elif cur_price <= 0.03 and size > 5:
            note = f"at ${cur_price:.3f}"

        # Only show live positions — skip resolved (>=97c) and dust (<0.5c)
        if cur_price < 0.97 and cur_price > 0.01 and size >= 1.0:
            positions_data.append({
                "label": label, "pnl": pnl, "cur_price": cur_price,
                "avg_price": avg_price, "size": size, "note": note,
            })

        # ── Instant alerts ──────────────────────────────────────
        if cur_price >= 0.99 and positions_state.get(token_id, {}).get("status") != "won":
            profit = (cur_price - avg_price) * size
            alerts.append(f"🏆 **RESOLVED WIN** | {title[:60]}\nEntry {avg_price:.3f} → 1.00 | **+${profit:.2f}**")
            positions_state.setdefault(token_id, {})["status"] = "won"

        elif cur_price <= 0.01 and positions_state.get(token_id, {}).get("status") != "lost":
            loss = avg_price * size
            alerts.append(f"💀 **RESOLVED LOSS** | {title[:60]}\nEntry {avg_price:.3f} → 0.00 | **-${loss:.2f}**")
            positions_state.setdefault(token_id, {})["status"] = "lost"

        elif last_price is not None:
            move = (cur_price - last_price) / last_price if last_price > 0 else 0
            if abs(move) >= MOVE_ALERT_PCT:
                direction = "📈" if move > 0 else "📉"
                pnl_str = f"+${abs(pnl):.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                alerts.append(f"{direction} **MOVE** | {label} | {last_price:.3f} → {cur_price:.3f} ({move:+.0%}) | **{pnl_str}**")

            if cur_price >= avg_price * WINNER_MULT and not positions_state.get(token_id, {}).get("winner_alerted"):
                gain = (cur_price - avg_price) * size
                alerts.append(f"🚀 **2X RUNNER** | {label} | Entry {avg_price:.3f} → {cur_price:.3f} | **+${gain:.2f}**")
                positions_state.setdefault(token_id, {})["winner_alerted"] = True

            if avg_price > 0 and cur_price <= avg_price * (1 - BLEED_ALERT_PCT) and not positions_state.get(token_id, {}).get("bleed_alerted"):
                loss_unreal = (avg_price - cur_price) * size
                alerts.append(f"🩸 **BLEED** | {label} | Entry {avg_price:.3f} → {cur_price:.3f} | **-${loss_unreal:.2f}**")
                positions_state.setdefault(token_id, {})["bleed_alerted"] = True

        positions_state.setdefault(token_id, {})["last_price"] = cur_price
        positions_state.setdefault(token_id, {})["avg_price"]  = avg_price
        positions_state.setdefault(token_id, {})["title"]      = title[:60]

    # Fire instant alerts first
    for alert in alerts:
        post_discord(alert)
        time.sleep(0.3)

    # Always post the full summary card
    summary = build_summary(positions_data, now_str, len(alerts))
    post_discord(summary)
    print(summary)

    save_state({"positions": positions_state})

if __name__ == "__main__":
    main()
