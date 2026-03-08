# Polymarket Weather Arb

Automated weather arbitrage bot for [Polymarket](https://polymarket.com) temperature + precipitation markets.

## How It Works

1. **Scanner** — fetches live Polymarket odds and compares against Open-Meteo weather forecasts using a normal distribution model
2. **Auto Trader** — places BUY orders on markets where the model probability diverges from Polymarket's implied odds by >20%
3. **Dashboard** — live P&L tracking at [kanawatiomar.github.io/polymarket-dashboard](https://kanawatiomar.github.io/polymarket-dashboard)

## Strategy

- Source: [Open-Meteo](https://open-meteo.com) forecasts (free, no API key)
- Edge threshold: >5% to flag, >20% to auto-trade
- Markets: daily high temperature across 17 cities worldwide
- Sizing: $3/trade, max 2 positions per run
- Runs: 8 AM + 4 PM MDT daily (automated)

## Setup

### 1. Install dependencies
```bash
pip install requests "httpx[http2]" scipy numpy py-clob-client
```

### 2. Configure credentials
Create `.env`:
```
POLY_PRIVATE_KEY=your_private_key_here
POLY_ADDRESS=your_wallet_address
# POLY_PROXY=http://user:pass@host:port/  # optional, needed if geo-blocked
```

Create `creds.json` (from Polymarket API dashboard):
```json
{
  "apiKey": "...",
  "secret": "...",
  "passphrase": "..."
}
```

### 3. Run

**Scan for edges:**
```bash
python scanner.py --days 3 --min-edge 0.05 --min-vol 500
```

**Auto trade top edges:**
```bash
python auto_trade.py
```

**Manual trade placement:**
```bash
python trade_now.py
```

## Files

| File | Description |
|------|-------------|
| `scanner.py` | Main scanner — fetches odds + weather, finds edges |
| `auto_trade.py` | Auto-places top trades from scan_results.json |
| `trade_now.py` | Manual trade execution |
| `trader.py` | CLI for checking balances, orders, positions |
| `backtest.py` | Historical backtesting |
| `place_trades.py` | Batch trade placement |
| `run_auto.ps1` | Windows automation script (scan + trade + push) |

## Cities Covered

NYC, London, Seoul, Miami, Chicago, Seattle, Dallas, Atlanta, Wellington, Toronto, Paris, Ankara, Buenos Aires, Lucknow, Munich, São Paulo, Los Angeles

## Notes

- Polymarket geo-blocks US IPs on the CLOB (order placement). Use a VPN or residential proxy.
- Scanner and dashboard work without proxy (public APIs only).
- All credentials stay local — never committed to git.
