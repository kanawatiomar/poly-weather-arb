"""
position_monitor.py -- Discord alerts on big position moves.
Tracks last known price per token, fires alert when:
  - Position moves +/-20% from last check (momentum alert)
  - Position is up 2x from entry (winner alert)
  - Position drops 60%+ from entry (bleed alert)
  - Order fully fills (fill alert)
  - Market resolves (settlement alert)

Run every 15-30 min via cron.
"""
import json, httpx, time
from pathlib import Path
from datetime import datetime

BASE        = Path(__file__).parent
STATE_FILE  = BASE / "monitor_state.json"
DISCORD_CHANNEL_ID = "1479364504943857684"

MOVE_ALERT_PCT   = 0.20   # alert if price moves 20%+ since last check
WINNER_MULT      = 2.0    # alert if current_price >= entry * WINNER_MULT
BLEED_ALERT_PCT  = 0.60   # alert if current_price dropped 60%+ from entry

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

def post_discord(msg):
    env = load_env()
    token = env.get("DISCORD_BOT_TOKEN","")
    if not token:
        print(f"  [Discord] No DISCORD_BOT_TOKEN in .env, skipping")
        return False
    try:
        r = httpx.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"content": msg},
            timeout=8,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Discord error: {e}")
        return False

def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def fetch_wallet_positions():
    env = load_env()
    wallet = env.get("POLY_ADDRESS","")
    try:
        r = httpx.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet, "sizeThreshold": "0.01"},
            timeout=10
        )
        if r.is_success:
            return r.json()
    except: pass
    return []

def main():
    state = load_state()
    positions_state = state.get("positions", {})
    alerts = []

    positions = fetch_wallet_positions()
    print(f"[Monitor] {datetime.now().strftime('%H:%M:%S')} -- {len(positions)} positions")

    for pos in positions:
        token_id  = pos.get("asset") or pos.get("tokenId", "")
        size      = float(pos.get("size", 0))
        avg_price = float(pos.get("avgPrice", 0) or pos.get("averagePrice", 0) or 0)
        title     = pos.get("title") or pos.get("question") or token_id[:20]

        if size < 0.5 or not token_id:
            continue

        cur_price = get_price(token_id)
        if cur_price is None:
            continue

        last_price = positions_state.get(token_id, {}).get("last_price")
        pnl        = (cur_price - avg_price) * size
        pnl_pct    = (cur_price - avg_price) / avg_price if avg_price > 0 else 0

        print(f"  {title[:45]}: entry={avg_price:.3f} now={cur_price:.3f} pnl={pnl:+.2f}")

        # -- Resolution alerts
        if cur_price >= 0.99 and positions_state.get(token_id, {}).get("status") != "won":
            profit = (cur_price - avg_price) * size
            emoji  = "🏆" if profit > 0 else "🔔"
            alerts.append(
                f"{emoji} **RESOLVED WIN** | {title[:60]}\n"
                f"Entry {avg_price:.3f} → 1.00 | Size {size:.1f} | **+${profit:.2f}**"
            )
            positions_state.setdefault(token_id, {})["status"] = "won"

        elif cur_price <= 0.01 and positions_state.get(token_id, {}).get("status") != "lost":
            loss = avg_price * size
            alerts.append(
                f"💀 **RESOLVED LOSS** | {title[:60]}\n"
                f"Entry {avg_price:.3f} → 0.00 | Size {size:.1f} | **-${loss:.2f}**"
            )
            positions_state.setdefault(token_id, {})["status"] = "lost"

        elif last_price is not None:
            move = (cur_price - last_price) / last_price if last_price > 0 else 0

            # Big move since last check
            if abs(move) >= MOVE_ALERT_PCT:
                direction = "📈" if move > 0 else "📉"
                pnl_str   = f"+${abs(pnl):.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                alerts.append(
                    f"{direction} **MOVE ALERT** | {title[:60]}\n"
                    f"{last_price:.3f} → {cur_price:.3f} ({move:+.0%} since last check) | P&L: **{pnl_str}**"
                )

            # Winner: doubled from entry
            if cur_price >= avg_price * WINNER_MULT and not positions_state.get(token_id, {}).get("winner_alerted"):
                gain = (cur_price - avg_price) * size
                alerts.append(
                    f"🚀 **2X RUNNER** | {title[:60]}\n"
                    f"Entry {avg_price:.3f} → {cur_price:.3f} | **+${gain:.2f}** | Size {size:.1f}"
                )
                positions_state.setdefault(token_id, {})["winner_alerted"] = True

            # Bleed alert: down 60% from entry
            if avg_price > 0 and cur_price <= avg_price * (1 - BLEED_ALERT_PCT) and not positions_state.get(token_id, {}).get("bleed_alerted"):
                loss_unreal = (avg_price - cur_price) * size
                alerts.append(
                    f"🩸 **BLEED ALERT** | {title[:60]}\n"
                    f"Entry {avg_price:.3f} → {cur_price:.3f} ({pnl_pct:.0%}) | Unrealized: **-${loss_unreal:.2f}**"
                )
                positions_state.setdefault(token_id, {})["bleed_alerted"] = True

        # Update last price
        positions_state.setdefault(token_id, {})["last_price"]  = cur_price
        positions_state.setdefault(token_id, {})["avg_price"]   = avg_price
        positions_state.setdefault(token_id, {})["title"]       = title[:60]

    # Send alerts
    if alerts:
        print(f"  Sending {len(alerts)} alert(s)...")
        for alert in alerts:
            post_discord(alert)
            time.sleep(0.5)
    else:
        print("  No alerts.")

    save_state({"positions": positions_state})

if __name__ == "__main__":
    main()
