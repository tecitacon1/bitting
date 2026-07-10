#!/usr/bin/env python3
"""Local viewer: weather forecast + Polymarket Seoul temperature outcomes."""

from datetime import datetime, timedelta, UTC

from config import CHECK_TODAY, CITY, FORECAST_LOCATION_NAME, MARKET_DAYS_AHEAD
from forecast_engine import build_forecast, parse_date_from_slug
from market import format_outcome_label, get_event_by_slug, parse_event_to_ranges
from model import probability_of_market

LINE = "─" * 72
DOUBLE = "═" * 72


def generate_slugs(city="seoul", days_ahead=2, include_today=True):
    slugs = []
    today = datetime.now(UTC)
    start_offset = 0 if include_today else 1

    for i in range(start_offset, days_ahead + 1):
        target_date = today + timedelta(days=i)
        month = target_date.strftime("%B").lower()
        day = target_date.day
        year = target_date.year
        slugs.append(f"highest-temperature-in-{city}-on-{month}-{day}-{year}")

    return slugs


def pct(value):
    return f"{value * 100:5.1f}%"


def edge_str(model_prob, market_prob):
    edge = model_prob - market_prob
    sign = "+" if edge >= 0 else ""
    return f"{sign}{edge * 100:5.1f}%"


def print_header(title):
    print(f"\n{DOUBLE}")
    print(f"  {title}")
    print(DOUBLE)


def print_forecast(forecast):
    print(f"\n  Location:     {FORECAST_LOCATION_NAME}")
    print(f"  Expected max: {forecast.expected_value:.1f}°C")
    print(f"  Uncertainty:  σ = {forecast.std_dev:.2f}°C")
    if forecast.confidence is not None:
        print(f"  Confidence:   {forecast.confidence * 100:.0f}%")

    if forecast.forecast_models:
        print(f"\n  Model forecasts:")
        for name, value in sorted(forecast.forecast_models.items()):
            weight = forecast.model_weights.get(name, 0)
            print(f"    {name:<14} {value:5.1f}°C  (weight {weight * 100:.0f}%)")

    if forecast.risk_flags:
        print(f"\n  ⚠ Risk flags: {', '.join(forecast.risk_flags)}")


def print_outcomes(ranges, mean, std):
    rows = []
    for bucket in ranges:
        if bucket.get("closed"):
            continue
        label = format_outcome_label(bucket)
        model_prob = probability_of_market(bucket, mean, std)
        market_prob = bucket["yes_price"]
        rows.append({
            "label": label,
            "model": model_prob,
            "market": market_prob,
            "edge": model_prob - market_prob,
            "resolved": bucket.get("resolved"),
            "winner": bucket.get("winning_outcome"),
        })

    if not rows:
        print("\n  No open outcomes found.")
        return

    print(f"\n  {'Outcome':<22} {'Model':>7} {'Market':>7} {'Edge':>8}")
    print(f"  {LINE}")

    for row in rows:
        marker = ""
        if row["resolved"] and row["winner"]:
            marker = f"  ← resolved {row['winner']}"
        elif row["model"] >= 0.15:
            marker = "  ← model favorite"

        print(
            f"  {row['label']:<22} {pct(row['model']):>7} {pct(row['market']):>7} "
            f"{edge_str(row['model'], row['market']):>8}{marker}"
        )

    best_model = max(rows, key=lambda r: r["model"])
    best_market = max(rows, key=lambda r: r["market"])
    print(f"\n  Model pick:  {best_model['label']} ({pct(best_model['model'])})")
    print(f"  Market pick: {best_market['label']} ({pct(best_market['market'])})")


def analyze_event(slug):
    event = get_event_by_slug(slug)
    if not event:
        print(f"\n  ✗ No Polymarket event found for: {slug}")
        return

    target_date = parse_date_from_slug(slug)
    if not target_date:
        print(f"\n  ✗ Could not parse date from slug: {slug}")
        return

    title = event.get("title") or slug.replace("-", " ").title()
    print_header(title)

    ranges = parse_event_to_ranges(event)
    if not ranges:
        print("\n  ✗ Event found but no temperature buckets parsed.")
        return

    forecast = build_forecast(target_date, ranges)
    if forecast.market_adjusted_forecast is None:
        print("\n  ✗ Weather forecast unavailable for this date.")
        return

    print_forecast(forecast)
    print_outcomes(ranges, forecast.mean, forecast.std)


def main():
    city = CITY.lower()
    slugs = generate_slugs(city, MARKET_DAYS_AHEAD, CHECK_TODAY)

    print(f"\n{CITY} weather × Polymarket  |  {len(slugs)} day(s)  |  {datetime.now(UTC):%Y-%m-%d %H:%M UTC}")

    for slug in slugs:
        analyze_event(slug)

    print(f"\n{DOUBLE}\n")


if __name__ == "__main__":
    main()
