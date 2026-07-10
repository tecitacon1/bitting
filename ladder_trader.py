#!/usr/bin/env python3
"""Live ladder trader: follow RKSI running daily max on Polymarket (Railway worker)."""

import logging
import sys
import time
from datetime import datetime, timezone

from config import (
    CITY,
    LADDER_POLL_INTERVAL_SEC,
    LIVE_TRADING_ENABLED,
    POLYMARKET_PRIVATE_KEY,
)
from execution_engine import PolymarketExecutor, default_order_usdc
from forecast_engine import parse_date_from_slug
from ladder_strategy import compute_forecast_gates, decide_ladder_action
from live_rksi import get_live_temperature_snapshot, seoul_now
from live_state import LadderStateStore
from market_trade import load_trade_event, slug_for_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("ladder_trader")


def _city_slug():
    return CITY.lower().replace(" ", "-")


def _ensure_day_state(store: LadderStateStore, slug, local_date, buckets):
    state = store.get()
    if state.get("slug") == slug and state.get("local_date") == local_date:
        return state

    target_date = parse_date_from_slug(slug)
    ranges = [
        {
            "temp": bucket["temp"],
            "type": bucket["type"],
            "yes_price": bucket["yes_price"],
            "closed": bucket.get("closed"),
        }
        for bucket in buckets
    ]
    entry_floor, ceiling_temp, forecast = compute_forecast_gates(target_date, ranges)
    store.reset_for_day(slug, local_date, entry_floor=entry_floor, ceiling_temp=ceiling_temp)
    LOGGER.info(
        "New trading day | slug=%s | entry_floor=%s | ceiling=%s | forecast=%.1f°C",
        slug,
        entry_floor,
        ceiling_temp,
        forecast.market_adjusted_forecast or 0.0,
    )
    return store.get()


def _apply_running_max_state(store: LadderStateStore, snapshot):
    state = store.get()
    running_max = snapshot.running_daily_max_c
    if running_max is None:
        return state

    last_seen = state.get("running_max_seen")
    updates = {"running_max_seen": running_max}
    if last_seen is None or running_max > last_seen:
        updates["last_running_max_at"] = datetime.now(timezone.utc).isoformat()
        updates["peak_locked"] = False
    elif last_seen == running_max and _stall_minutes(state) is not None:
        from config import LADDER_PEAK_STALL_MINUTES

        if _stall_minutes(state) >= LADDER_PEAK_STALL_MINUTES:
            updates["peak_locked"] = True
    store.update(**updates)
    return store.get()


def _stall_minutes(state):
    last_at = state.get("last_running_max_at")
    if not last_at:
        return None
    last_dt = datetime.fromisoformat(last_at)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - last_dt
    return delta.total_seconds() / 60.0


def _execute_decision(executor: PolymarketExecutor, store: LadderStateStore, decision):
    state = store.get()
    if decision.action in {"wait", "hold"}:
        return

    if decision.action == "enter":
        result = executor.market_buy_yes(decision.buy_token_id, decision.buy_usdc)
        if not result.get("ok"):
            LOGGER.warning("Enter skipped | reason=%s", result.get("reason"))
            return
        bucket = decision.target_bucket
        store.update(
            status="holding",
            held_bucket_temp=bucket["temp"],
            held_bucket_label=bucket["label"],
            held_token_id=bucket["token_id_yes"],
            held_shares=float(result.get("shares") or 0.0),
            last_upgrade_at=datetime.now(timezone.utc).isoformat(),
        )
        store.append_trade({"action": "enter", **result, "label": bucket["label"]})
        LOGGER.info("Entered %s | shares=%.4f | price=%.3f", bucket["label"], result["shares"], result["price"])
        return

    if decision.action == "upgrade":
        sell_result = {"ok": True, "shares": 0.0, "price": 0.0}
        if decision.sell_shares > 0 and decision.sell_token_id:
            sell_result = executor.market_sell_yes(decision.sell_token_id, decision.sell_shares)
            if not sell_result.get("ok"):
                LOGGER.warning("Upgrade sell failed | reason=%s", sell_result.get("reason"))
                return
            store.append_trade(
                {
                    "action": "sell",
                    **sell_result,
                    "label": state.get("held_bucket_label"),
                }
            )

        buy_result = executor.market_buy_yes(decision.buy_token_id, decision.buy_usdc)
        if not buy_result.get("ok"):
            LOGGER.warning("Upgrade buy failed | reason=%s", buy_result.get("reason"))
            return

        bucket = decision.target_bucket
        store.update(
            status="holding",
            held_bucket_temp=bucket["temp"],
            held_bucket_label=bucket["label"],
            held_token_id=bucket["token_id_yes"],
            held_shares=float(buy_result.get("shares") or 0.0),
            last_upgrade_at=datetime.now(timezone.utc).isoformat(),
        )
        store.append_trade({"action": "upgrade_buy", **buy_result, "label": bucket["label"]})
        LOGGER.info(
            "Upgraded to %s | sold=%.4f @ %.3f | bought=%.4f @ %.3f",
            bucket["label"],
            sell_result.get("shares", 0.0),
            sell_result.get("price", 0.0),
            buy_result.get("shares", 0.0),
            buy_result.get("price", 0.0),
        )


def run_cycle(executor: PolymarketExecutor, store: LadderStateStore):
    now = seoul_now()
    local_date = now.date().isoformat()
    slug = slug_for_date(_city_slug(), now.date())

    event, buckets = load_trade_event(slug)
    if not event or not buckets:
        LOGGER.warning("No active event for slug=%s", slug)
        return

    state = _ensure_day_state(store, slug, local_date, buckets)
    snapshot = get_live_temperature_snapshot(now.date())
    state = _apply_running_max_state(store, snapshot)

    decision = decide_ladder_action(
        snapshot,
        buckets,
        state,
        order_usdc=default_order_usdc(),
        now=now,
    )

    LOGGER.info(
        "Cycle | kst=%s | current=%s°C | running_max=%s°C | bucket=%s | held=%s | action=%s | reason=%s",
        now.strftime("%H:%M"),
        snapshot.current_temp_c,
        snapshot.running_daily_max_c,
        snapshot.resolved_bucket_temp,
        state.get("held_bucket_label"),
        decision.action,
        decision.reason,
    )

    _execute_decision(executor, store, decision)


def main():
    if LIVE_TRADING_ENABLED and not POLYMARKET_PRIVATE_KEY:
        print("POLYMARKET_PRIVATE_KEY is required when LIVE_TRADING_ENABLED=true", file=sys.stderr)
        sys.exit(1)

    executor = PolymarketExecutor()
    store = LadderStateStore()

    LOGGER.info(
        "Ladder trader started | city=%s | live=%s | poll=%ss",
        CITY,
        LIVE_TRADING_ENABLED,
        LADDER_POLL_INTERVAL_SEC,
    )

    while True:
        try:
            run_cycle(executor, store)
        except Exception:
            LOGGER.exception("Cycle failed")
        time.sleep(LADDER_POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
