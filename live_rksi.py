"""Live RKSI temperature observer (Wunderground / Weather.com API)."""

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests

from config import WUNDERGROUND_API_KEY, WUNDERGROUND_STATION_ID
from wunderground_rksi import (
    DEFAULT_WU_API_KEY,
    WU_MAX_RETRIES,
    WU_REQUEST_GAP_SEC,
    WU_RETRY_BACKOFF_SEC,
    _wu_headers,
    daily_max_from_observations,
    polymarket_bucket_temp,
)

LOGGER = logging.getLogger("live_rksi")

SEOUL_TZ = ZoneInfo("Asia/Seoul")
WU_CURRENT_URL = "https://api.weather.com/v1/location/{station}/observations/current.json"
WU_HISTORICAL_URL = (
    "https://api.weather.com/v1/location/{station}/observations/historical.json"
)


@dataclass
class LiveTemperatureSnapshot:
    local_date: str
    current_temp_c: float | None
    running_daily_max_c: float | None
    resolved_bucket_temp: int | None
    observation_time_local: str | None
    source: str = "wunderground_rksi"


def _wu_api_key():
    return WUNDERGROUND_API_KEY or DEFAULT_WU_API_KEY


def _request_json(url, params):
    last_status = None
    for attempt in range(1, WU_MAX_RETRIES + 1):
        response = requests.get(
            url,
            params=params,
            headers=_wu_headers(),
            timeout=30,
        )
        last_status = response.status_code
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429 and attempt < WU_MAX_RETRIES:
            wait_sec = WU_RETRY_BACKOFF_SEC * (2 ** (attempt - 1))
            LOGGER.warning("Live RKSI 429 | retry in %.1fs", wait_sec)
            time.sleep(wait_sec)
            continue
        break
    LOGGER.warning("Live RKSI request failed | status=%s | url=%s", last_status, url)
    return None


def _extract_current_temp(payload):
    if not payload:
        return None, None
    observation = payload.get("observation") or {}
    metric = observation.get("metric") or {}
    temp = metric.get("temp")
    if temp is None:
        temp = observation.get("temp")
    obs_time_local = observation.get("obs_time_local")
    if temp is None:
        return None, obs_time_local
    return float(temp), obs_time_local


def fetch_current_observation():
    params = {"apiKey": _wu_api_key(), "units": "m"}
    url = WU_CURRENT_URL.format(station=WUNDERGROUND_STATION_ID)
    payload = _request_json(url, params)
    time.sleep(WU_REQUEST_GAP_SEC)
    return _extract_current_temp(payload)


def fetch_today_observation_max(local_day=None):
    local_day = local_day or datetime.now(SEOUL_TZ).date()
    params = {
        "apiKey": _wu_api_key(),
        "units": "m",
        "startDate": local_day.strftime("%Y%m%d"),
        "endDate": local_day.strftime("%Y%m%d"),
    }
    url = WU_HISTORICAL_URL.format(station=WUNDERGROUND_STATION_ID)
    payload = _request_json(url, params)
    time.sleep(WU_REQUEST_GAP_SEC)
    if not payload:
        return {}
    observations = payload.get("observations", [])
    return daily_max_from_observations(observations)


def get_live_temperature_snapshot(local_day=None) -> LiveTemperatureSnapshot:
    local_day = local_day or datetime.now(SEOUL_TZ).date()
    current_temp, obs_time_local = fetch_current_observation()
    today_max_map = fetch_today_observation_max(local_day)
    today_key = local_day.isoformat()
    running_max = today_max_map.get(today_key)

    temps = [value for value in (running_max, current_temp) if value is not None]
    if temps:
        running_max = max(temps)

    resolved_bucket = polymarket_bucket_temp(running_max) if running_max is not None else None
    return LiveTemperatureSnapshot(
        local_date=today_key,
        current_temp_c=current_temp,
        running_daily_max_c=running_max,
        resolved_bucket_temp=resolved_bucket,
        observation_time_local=obs_time_local,
    )


def seoul_now():
    return datetime.now(SEOUL_TZ)
