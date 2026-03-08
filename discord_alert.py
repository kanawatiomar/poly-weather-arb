"""
discord_alert.py -- Shared Discord webhook helper.
All scripts import post_discord() from here.
Webhook URL is read from .env — no bot token needed, never expires.
"""
import httpx
from pathlib import Path

BASE = Path(__file__).parent

def _get_webhook():
    for line in (BASE / ".env").read_text().splitlines():
        if line.startswith("DISCORD_WEBHOOK="):
            return line.split("=", 1)[1].strip()
    return None

def post_discord(msg: str, _token=None) -> bool:
    """Post a message to #poly via webhook. _token param ignored (legacy compat)."""
    url = _get_webhook()
    if not url:
        print("  [Discord] No DISCORD_WEBHOOK in .env")
        return False
    try:
        r = httpx.post(url, json={"content": msg[:1990]}, timeout=8)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  [Discord] Error: {e}")
        return False
