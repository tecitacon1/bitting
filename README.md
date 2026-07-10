# Seoul Weather × Polymarket

Forecast viewer plus a **live ladder trader** that follows RKSI (Wunderground) temperature updates into Polymarket buckets.

## Two modes

| Command | Purpose |
|---------|---------|
| `python main.py` | Read-only forecast vs market report |
| `python ladder_trader.py` | Live ladder worker (Railway) |

The forecast pipeline (`forecast_engine.py`, calibration) is **not** used for trade signals — only for entry floor / ceiling gates. Trading follows the **running daily max** from RKSI observations.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Viewer

```bash
python main.py
```

### Ladder trader (paper mode)

```bash
LIVE_TRADING_ENABLED=false python ladder_trader.py
```

Logs buy/sell decisions without placing orders.

### Ladder trader (live)

```bash
LIVE_TRADING_ENABLED=true \
POLYMARKET_PRIVATE_KEY=0x... \
POLYMARKET_FUNDER_ADDRESS=0x... \
python ladder_trader.py
```

---

## Railway deploy

1. Connect repo to Railway
2. Set env vars from `.env.example` (wallet keys + `LIVE_TRADING_ENABLED=true`)
3. `Procfile` runs `python ladder_trader.py`

Recommended Railway variables:

```bash
LIVE_TRADING_ENABLED=true
POLYMARKET_PRIVATE_KEY=...
POLYMARKET_FUNDER_ADDRESS=...
POLYMARKET_SIGNATURE_TYPE=3
CITY=Seoul
LADDER_POLL_INTERVAL_SEC=45
LADDER_ORDER_USDC=10
LADDER_MAX_BUY_PRICE=0.85
```

---

## How the ladder works

1. Poll RKSI current + today’s observations every `LADDER_POLL_INTERVAL_SEC`
2. Track `running_daily_max` (max temperature seen today, KST)
3. Map to Polymarket bucket via `int(round(max))`
4. **Enter** when running max crosses forecast entry floor (e.g. 28°C)
5. **Upgrade** when max increases: sell old bucket YES → buy new bucket YES
6. **Hold** when temperature drops (daily max never decreases)
7. **Lock** after max unchanged for `LADDER_PEAK_STALL_MINUTES`

Trading window: `LADDER_TRADE_START_HOUR_KST`–`LADDER_TRADE_END_HOUR_KST` (default 10:00–18:00 KST).

---

## Project layout

| File | Role |
|------|------|
| `ladder_trader.py` | Railway worker entry point |
| `live_rksi.py` | Live Wunderground RKSI observer |
| `ladder_strategy.py` | Ladder state machine |
| `execution_engine.py` | Polymarket CLOB buy/sell |
| `market_trade.py` | Bucket + token ID mapping |
| `live_state.py` | Persistent ladder position state |
| `forecast_engine.py` | Forecast (read-only gates only) |
| `market_calibration.py` | Market resolution delta |
| `wunderground_rksi.py` | RKSI historical actuals |
| `market.py` | Polymarket Gamma API |
| `model.py` | Probability math |
| `config.py` | Environment configuration |
| `main.py` | Optional local forecast viewer |

Runtime caches (auto-regenerated): `forecast_calibration.json`, `market_calibration.json`, `rksi_actuals.json`, `ladder_state.json`

---

## Risk notes

- Everyone watches the same Wunderground page — edge is speed + execution
- Each ladder step pays spread/fees
- Use `LADDER_MAX_BUY_PRICE` to avoid buying near-certain buckets
- Start with `LIVE_TRADING_ENABLED=false` and verify logs
