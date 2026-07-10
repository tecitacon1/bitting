"""Polymarket bucket metadata for live ladder trading."""

import re

from market import extract_resolution, format_outcome_label, get_event_by_slug, parse_float, parse_json_field


def slug_for_date(city_slug, target_date):
    month = target_date.strftime("%B").lower()
    return (
        f"highest-temperature-in-{city_slug}-on-{month}-{target_date.day}-{target_date.year}"
    )


def parse_event_to_trade_buckets(event):
    """Parse temperature buckets with CLOB token ids for trading."""
    markets = event.get("markets", [])
    buckets = []

    for market in markets:
        question = market.get("question", "").lower()
        prices = parse_json_field(market.get("outcomePrices"), [])
        token_ids = parse_json_field(market.get("clobTokenIds"), [])
        if not prices or len(token_ids) < 2:
            continue

        yes_price = parse_float(prices[0])
        if yes_price is None:
            continue

        temp = None
        market_type = "exact"
        match = re.search(r"(\d+)\s*[°º]?\s*c", question)
        if match:
            temp = int(match.group(1))
        if "below" in question:
            market_type = "below_or_equal"
        elif "higher" in question:
            market_type = "above_or_equal"
        if temp is None:
            continue

        resolved, winning_outcome = extract_resolution(market, yes_price)
        buckets.append(
            {
                "temp": temp,
                "type": market_type,
                "label": format_outcome_label(
                    {"temp": temp, "type": market_type}
                ),
                "yes_price": yes_price,
                "no_price": 1 - yes_price,
                "question": market.get("question", ""),
                "market_id": str(market.get("id", "")),
                "condition_id": market.get("conditionId", ""),
                "token_id_yes": str(token_ids[0]),
                "token_id_no": str(token_ids[1]),
                "closed": bool(market.get("closed") or market.get("archived")),
                "resolved": resolved,
                "winning_outcome": winning_outcome,
                "accepting_orders": bool(market.get("acceptingOrders", True)),
            }
        )

    buckets.sort(key=lambda item: (item["type"], item["temp"]))
    return buckets


def bucket_for_daily_max(temp_c, buckets):
    """Map observed daily max (°C) to the Polymarket bucket that should win."""
    if temp_c is None:
        return None
    rounded = int(round(float(temp_c)))

    exact = [bucket for bucket in buckets if bucket["type"] == "exact" and bucket["temp"] == rounded]
    if exact:
        return exact[0]

    above = [
        bucket
        for bucket in buckets
        if bucket["type"] == "above_or_equal" and rounded >= bucket["temp"]
    ]
    if above:
        return max(above, key=lambda item: item["temp"])

    below = [
        bucket
        for bucket in buckets
        if bucket["type"] == "below_or_equal" and rounded <= bucket["temp"]
    ]
    if below:
        return min(below, key=lambda item: item["temp"])
    return None


def load_trade_event(slug):
    event = get_event_by_slug(slug)
    if not event:
        return None, []
    return event, parse_event_to_trade_buckets(event)
