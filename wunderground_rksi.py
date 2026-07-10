import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from config import (
    FORECAST_BIAS_LOOKBACK_DAYS,
    RKSI_ACTUALS_CACHE_FILE,
    WUNDERGROUND_API_KEY,
    WUNDERGROUND_STATION_ID,
)

LOGGER = logging.getLogger("wunderground_rksi")

SEOUL_TZ = ZoneInfo("Asia/Seoul")
WU_HISTORICAL_URL = (
    "https://api.weather.com/v1/location/{station}/observations/historical.json"
)
WU_MAX_RANGE_DAYS = 31
WU_REQUEST_GAP_SEC = 0.4
WU_MAX_RETRIES = 4
WU_RETRY_BACKOFF_SEC = 2.0

DEFAULT_WU_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


def _wu_headers():
    return {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.wunderground.com",
        "Referer": "https://www.wunderground.com/history/daily/kr/incheon/RKSI",
    }


def _wu_api_key():
    return WUNDERGROUND_API_KEY or DEFAULT_WU_API_KEY


def _request_historical(start_date, end_date):
    params = {
        "apiKey": _wu_api_key(),
        "units": "m",
        "startDate": start_date.strftime("%Y%m%d"),
        "endDate": end_date.strftime("%Y%m%d"),
    }
    url = WU_HISTORICAL_URL.format(station=WUNDERGROUND_STATION_ID)
    last_status = None
    for attempt in range(1, WU_MAX_RETRIES + 1):
        response = requests.get(url, params=params, headers=_wu_headers(), timeout=30)
        last_status = response.status_code
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429 and attempt < WU_MAX_RETRIES:
            wait_sec = WU_RETRY_BACKOFF_SEC * (2 ** (attempt - 1))
            LOGGER.warning("Wunderground 429 | retry in %.1fs", wait_sec)
            time.sleep(wait_sec)
            continue
        break
    LOGGER.warning(
        "Wunderground historical request failed | status=%s | %s..%s",
        last_status,
        start_date,
        end_date,
    )
    return None


def daily_max_from_observations(observations):
    by_day = defaultdict(list)
    for observation in observations:
        timestamp = observation.get("valid_time_gmt")
        temperature = observation.get("temp")
        if timestamp is None or temperature is None:
            continue
        local_day = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(SEOUL_TZ).date()
        by_day[local_day.isoformat()].append(float(temperature))

    return {
        day: round(max(temperatures), 1)
        for day, temperatures in by_day.items()
        if temperatures
    }


def fetch_daily_max_range(start_date, end_date):
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    actuals = {}
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=WU_MAX_RANGE_DAYS - 1), end_date)
        payload = _request_historical(chunk_start, chunk_end)
        time.sleep(WU_REQUEST_GAP_SEC)
        if payload:
            observations = payload.get("observations", [])
            actuals.update(daily_max_from_observations(observations))
        chunk_start = chunk_end + timedelta(days=1)

    return actuals


def _load_actuals_cache():
    if not os.path.exists(RKSI_ACTUALS_CACHE_FILE):
        return None
    try:
        with open(RKSI_ACTUALS_CACHE_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _save_actuals_cache(actuals):
    with open(RKSI_ACTUALS_CACHE_FILE, "w", encoding="utf-8") as handle:
        json.dump(actuals, handle, indent=2)


def get_rksi_actuals(force_refresh=False, lookback_days=None):
    lookback_days = lookback_days or FORECAST_BIAS_LOOKBACK_DAYS
    cached = _load_actuals_cache()
    if cached and not force_refresh:
        age_hours = (
            datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])
        ).total_seconds() / 3600
        if age_hours < 12 and cached.get("actuals"):
            return cached["actuals"]

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days)
    actuals = fetch_daily_max_range(start_date, end_date)

    if not actuals and cached:
        return cached.get("actuals", {})

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "wunderground_rksi",
        "station": WUNDERGROUND_STATION_ID,
        "timezone": "Asia/Seoul",
        "actuals": actuals,
    }
    _save_actuals_cache(payload)
    return actuals


def polymarket_bucket_temp(daily_max_c):
    return int(round(float(daily_max_c)))
