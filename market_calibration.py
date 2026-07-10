import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

from config import (
    CITY,
    MARKET_CALIBRATION_CACHE_FILE,
    MARKET_CALIBRATION_LOOKBACK_DAYS,
)
from market import get_event_by_slug, parse_float, parse_json_field, extract_resolution
from wunderground_rksi import get_rksi_actuals, polymarket_bucket_temp

LOGGER = logging.getLogger("market_calibration")


def _slug_for_date(target_date, city=None):
    city = (city or CITY).lower()
    month = target_date.strftime("%B").lower()
    return f"highest-temperature-in-{city}-on-{month}-{target_date.day}-{target_date.year}"


def _parse_ranges_quiet(event):
    ranges = []
    for market in event.get("markets", []):
        question = market.get("question", "").lower()
        prices = parse_json_field(market.get("outcomePrices"), [])
        if not prices:
            continue
        yes_price = parse_float(prices[0])
        if yes_price is None:
            continue

        temp = None
        market_type = "exact"
        match = re.search(r"(\d+)\s*[°º]?\s*c", question)
        if match:
            temp = int(match.group(1))
        if "below" in question and match:
            market_type = "below_or_equal"
        elif "higher" in question and match:
            market_type = "above_or_equal"

        if temp is None:
            continue

        resolved, winning_outcome = extract_resolution(market, yes_price)
        ranges.append(
            {
                "temp": temp,
                "type": market_type,
                "resolved": resolved,
                "winning_outcome": winning_outcome,
            }
        )
    return ranges


def _resolution_temp_from_ranges(ranges):
    for market_range in ranges:
        if market_range.get("winning_outcome") != "YES":
            continue
        return int(market_range["temp"])
    return None


def _load_cache():
    if not os.path.exists(MARKET_CALIBRATION_CACHE_FILE):
        return None
    try:
        with open(MARKET_CALIBRATION_CACHE_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(payload):
    with open(MARKET_CALIBRATION_CACHE_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def collect_resolved_market_pairs(force_refresh=False, lookback_days=None):
    cached = _load_cache()
    if cached and not force_refresh:
        age_hours = (
            datetime.now(timezone.utc) - datetime.fromisoformat(cached["computed_at"])
        ).total_seconds() / 3600
        if age_hours < 12:
            return cached

    lookback_days = lookback_days or MARKET_CALIBRATION_LOOKBACK_DAYS
    actuals = get_rksi_actuals()
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days)

    pairs = []
    current = start_date
    while current <= end_date:
        slug = _slug_for_date(current)
        event = get_event_by_slug(slug)
        if event:
            ranges = _parse_ranges_quiet(event)
            resolution_temp = _resolution_temp_from_ranges(ranges)
            rksi_actual = actuals.get(current.isoformat())
            if resolution_temp is not None and rksi_actual is not None:
                rksi_bucket = polymarket_bucket_temp(rksi_actual)
                pairs.append(
                    {
                        "date": current.isoformat(),
                        "slug": slug,
                        "polymarket_resolution": resolution_temp,
                        "rksi_actual": round(float(rksi_actual), 1),
                        "rksi_bucket": rksi_bucket,
                        "delta": round(resolution_temp - rksi_bucket, 4),
                    }
                )
        current += timedelta(days=1)

    deltas = [pair["delta"] for pair in pairs]
    market_delta = float(sum(deltas) / len(deltas)) if deltas else 0.0
    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "market": "polymarket_seoul_temperature",
        "oracle_station": "RKSI",
        "lookback_days": lookback_days,
        "sample_size": len(pairs),
        "market_delta": round(market_delta, 4),
        "pairs": pairs,
    }
    _save_cache(payload)
    return payload


def get_market_delta(force_refresh=False):
    calibration = collect_resolved_market_pairs(force_refresh=force_refresh)
    return float(calibration.get("market_delta", 0.0)), calibration
