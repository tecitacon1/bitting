import json
import re

import requests

BASE_URL = "https://gamma-api.polymarket.com/events/slug/{}"


def get_event_by_slug(slug):
    url = BASE_URL.format(slug)
    res = requests.get(url, timeout=30)
    if res.status_code != 200:
        return None
    return res.json()


def parse_json_field(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def parse_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_outcome_label(temp_range):
    temp = temp_range["temp"]
    market_type = temp_range.get("type", "exact")
    if market_type == "below_or_equal":
        return f"{temp}°C or below"
    if market_type == "above_or_equal":
        return f"{temp}°C or higher"
    return f"{temp}°C"


def extract_resolution(market, yes_price):
    closed = bool(market.get("closed") or market.get("archived"))
    resolution_status = str(market.get("resolutionStatus") or "").lower()
    resolved = bool(
        closed
        or market.get("resolved")
        or resolution_status in {"resolved", "settled"}
    )

    winner = (
        market.get("winningOutcome")
        or market.get("winner")
        or market.get("resolvedOutcome")
        or market.get("resolution")
    )

    if isinstance(winner, dict):
        winner = winner.get("name") or winner.get("outcome")

    if winner is not None:
        normalized = str(winner).strip().lower()
        if normalized in {"yes", "y", "true", "1"}:
            return resolved, "YES"
        if normalized in {"no", "n", "false", "0"}:
            return resolved, "NO"

    outcomes = parse_json_field(market.get("outcomes"), [])
    outcome_prices = parse_json_field(market.get("outcomePrices"), [])
    if resolved and len(outcomes) == len(outcome_prices):
        priced_outcomes = [
            (str(outcome).strip().upper(), parse_float(price, 0.0))
            for outcome, price in zip(outcomes, outcome_prices)
        ]
        if priced_outcomes:
            outcome, price = max(priced_outcomes, key=lambda item: item[1])
            if price >= 0.99 and outcome in {"YES", "NO"}:
                return True, outcome

    if resolved and yes_price is not None:
        if yes_price >= 0.99:
            return True, "YES"
        if yes_price <= 0.01:
            return True, "NO"

    return resolved, None


def parse_event_to_ranges(event):
    """Parse Polymarket temperature buckets from an event payload."""
    markets = event.get("markets", [])
    if not markets:
        return []

    temps = []
    for market in markets:
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

        if "below" in question:
            market_type = "below_or_equal"
        elif "higher" in question:
            market_type = "above_or_equal"

        if temp is None:
            continue

        resolved, winning_outcome = extract_resolution(market, yes_price)
        temps.append({
            "temp": temp,
            "yes_price": yes_price,
            "no_price": 1 - yes_price,
            "type": market_type,
            "question": market.get("question", ""),
            "closed": bool(market.get("closed") or market.get("archived")),
            "resolved": resolved,
            "winning_outcome": winning_outcome,
        })

    temps.sort(key=lambda item: item["temp"])
    return temps
