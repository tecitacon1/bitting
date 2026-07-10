"""Live ladder strategy: follow RKSI running daily max into Polymarket buckets."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config import (
    LADDER_ENTRY_FLOOR_OFFSET,
    LADDER_MAX_BUY_PRICE,
    LADDER_PEAK_STALL_MINUTES,
    LADDER_TRADE_END_HOUR_KST,
    LADDER_TRADE_START_HOUR_KST,
)
from forecast_engine import build_forecast
from live_rksi import LiveTemperatureSnapshot, seoul_now
from market_trade import bucket_for_daily_max

LOGGER = logging.getLogger("ladder_strategy")


@dataclass
class LadderDecision:
    action: str
    reason: str
    target_bucket: dict | None = None
    sell_token_id: str | None = None
    sell_shares: float = 0.0
    buy_token_id: str | None = None
    buy_usdc: float = 0.0


def compute_forecast_gates(target_date, market_ranges):
    """Read-only forecast used only for entry floor and ceiling."""
    forecast = build_forecast(target_date, market_ranges)
    if forecast.market_adjusted_forecast is None:
        return None, None, forecast

    mean = float(forecast.mean or forecast.market_adjusted_forecast)
    std = float(forecast.std_dev or 1.0)
    entry_floor = int(mean - std) - LADDER_ENTRY_FLOOR_OFFSET
    ceiling_temp = int(mean + std) + 1
    return max(entry_floor, 0), ceiling_temp, forecast


def within_trading_window(now=None):
    now = now or seoul_now()
    hour = now.hour
    return LADDER_TRADE_START_HOUR_KST <= hour < LADDER_TRADE_END_HOUR_KST


def _peak_locked(state, snapshot: LiveTemperatureSnapshot, now):
    if state.get("peak_locked"):
        return True

    running_max = snapshot.running_daily_max_c
    if running_max is None:
        return False

    last_at = state.get("last_running_max_at")
    last_seen = state.get("running_max_seen")
    if last_at is None or last_seen is None:
        return False

    if running_max > last_seen:
        return False

    last_dt = datetime.fromisoformat(last_at)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    stall = now.astimezone(timezone.utc) - last_dt
    return stall >= timedelta(minutes=LADDER_PEAK_STALL_MINUTES)


def decide_ladder_action(
    snapshot: LiveTemperatureSnapshot,
    buckets,
    state: dict,
    order_usdc: float,
    now=None,
):
    now = now or seoul_now()

    if not within_trading_window(now):
        return LadderDecision("wait", "outside_trading_window")

    if snapshot.running_daily_max_c is None:
        return LadderDecision("wait", "no_temperature_data")

    open_buckets = [bucket for bucket in buckets if not bucket.get("closed")]
    if not open_buckets:
        return LadderDecision("wait", "market_closed")

    target_bucket = bucket_for_daily_max(snapshot.running_daily_max_c, open_buckets)
    if target_bucket is None:
        return LadderDecision("wait", "no_matching_bucket")

    entry_floor = state.get("entry_floor")
    ceiling_temp = state.get("ceiling_temp")
    resolved_temp = snapshot.resolved_bucket_temp

    if entry_floor is not None and resolved_temp is not None and resolved_temp < entry_floor:
        return LadderDecision(
            "wait",
            f"below_entry_floor ({resolved_temp} < {entry_floor})",
        )

    if ceiling_temp is not None and resolved_temp is not None and resolved_temp > ceiling_temp:
        return LadderDecision(
            "wait",
            f"above_forecast_ceiling ({resolved_temp} > {ceiling_temp})",
        )

    if target_bucket["yes_price"] > LADDER_MAX_BUY_PRICE:
        return LadderDecision(
            "wait",
            f"bucket_price_too_high ({target_bucket['yes_price']:.3f})",
            target_bucket=target_bucket,
        )

    if not target_bucket.get("accepting_orders", True):
        return LadderDecision("wait", "bucket_not_accepting_orders", target_bucket=target_bucket)

    held_temp = state.get("held_bucket_temp")
    held_token = state.get("held_token_id")
    held_shares = float(state.get("held_shares") or 0.0)

    if _peak_locked(state, snapshot, now):
        if held_temp == target_bucket["temp"] and state.get("held_bucket_label") == target_bucket["label"]:
            return LadderDecision("hold", "peak_locked", target_bucket=target_bucket)
        if held_temp is not None:
            return LadderDecision("hold", "peak_locked_keep_position", target_bucket=target_bucket)
        return LadderDecision("wait", "peak_locked_no_entry", target_bucket=target_bucket)

    if held_temp is None:
        return LadderDecision(
            "enter",
            "initial_bucket",
            target_bucket=target_bucket,
            buy_token_id=target_bucket["token_id_yes"],
            buy_usdc=order_usdc,
        )

    if held_temp == target_bucket["temp"] and state.get("held_bucket_label") == target_bucket["label"]:
        return LadderDecision("hold", "same_bucket", target_bucket=target_bucket)

    if target_bucket["temp"] > held_temp:
        return LadderDecision(
            "upgrade",
            "running_max_increased",
            target_bucket=target_bucket,
            sell_token_id=held_token,
            sell_shares=held_shares,
            buy_token_id=target_bucket["token_id_yes"],
            buy_usdc=order_usdc,
        )

    return LadderDecision("hold", "daily_max_does_not_decrease", target_bucket=target_bucket)
