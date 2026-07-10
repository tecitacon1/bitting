import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import requests

from config import (
    FORECAST_BIAS_LOOKBACK_DAYS,
    FORECAST_CALIBRATION_CACHE_FILE,
    FORECAST_HISTORICAL_ERROR_BUFFER,
    FORECAST_LATITUDE,
    FORECAST_LONGITUDE,
    FORECAST_MAX_STD_DEV,
    FORECAST_MAX_MODEL_WEIGHT,
    FORECAST_MIN_DOMINANT_WEIGHT,
    FORECAST_MIN_MODELS,
    FORECAST_SOFT_OUTLIER_TAU,
    FORECAST_STABILITY_VARIANCE_SCALE,
    METEOBLUE_API_KEY,
    OPENWEATHER_API_KEY,
)
from market_calibration import get_market_delta
from model import probability_of_market
from wunderground_rksi import get_rksi_actuals

LOGGER = logging.getLogger("forecast_engine")

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

WEATHER_MAX_RETRIES = 4
WEATHER_RETRY_BACKOFF_SEC = 2.0
REQUEST_GAP_SEC = 0.35
DEFAULT_UNCALIBRATED_MAE = 2.0

OPEN_METEO_MODELS = {
    "open_meteo": "best_match",
    "ecmwf": "ecmwf_ifs025",
    "gfs": "gfs_seamless",
}


@dataclass
class ForecastResult:
    target_date: str
    location: str = "RKSI"
    forecast_models: dict = field(default_factory=dict)
    model_biases: dict = field(default_factory=dict)
    corrected_models: dict = field(default_factory=dict)
    model_weights: dict = field(default_factory=dict)
    rksi_forecast: float | None = None
    market_adjusted_forecast: float | None = None
    market_delta: float = 0.0
    std_dev: float | None = None
    variance: float | None = None
    probability_distribution: dict = field(default_factory=dict)
    temperature_distribution: dict = field(default_factory=dict)
    expected_value: float | None = None
    confidence: float | None = None
    recommended_trade: dict | None = None
    tradeable: bool = False
    risk_flags: list = field(default_factory=list)
    mean: float | None = None
    std: float | None = None

    @property
    def final_temperature(self):
        return self.market_adjusted_forecast

    @property
    def uncertainty_reasons(self):
        return self.risk_flags

    def to_dict(self):
        return {
            "location": self.location,
            "target_date": self.target_date,
            "rkis_forecast": self.rksi_forecast,
            "market_adjusted_forecast": self.market_adjusted_forecast,
            "market_delta": round(self.market_delta, 4),
            "forecast_models": self.forecast_models,
            "model_biases": self.model_biases,
            "corrected_models": self.corrected_models,
            "model_weights": self.model_weights,
            "std_dev": self.std_dev,
            "variance": self.variance,
            "probability_distribution": self.probability_distribution,
            "temperature_distribution": self.temperature_distribution,
            "expected_value": self.expected_value,
            "confidence": self.confidence,
            "recommended_trade": self.recommended_trade,
            "tradeable": self.tradeable,
            "risk_flags": self.risk_flags,
        }


def parse_date_from_slug(slug):
    parts = slug.rsplit("-on-", 1)
    if len(parts) != 2:
        return None
    month, day, year = parts[1].split("-")
    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    month_num = month_map.get(month.lower())
    if not month_num:
        return None
    return date(int(year), month_num, int(day))


def _request_json(url, params):
    last_status = None
    for attempt in range(1, WEATHER_MAX_RETRIES + 1):
        response = requests.get(url, params=params, timeout=30)
        last_status = response.status_code
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429 and attempt < WEATHER_MAX_RETRIES:
            wait_sec = WEATHER_RETRY_BACKOFF_SEC * (2 ** (attempt - 1))
            LOGGER.warning("Weather API 429 | retry in %.1fs | url=%s", wait_sec, url)
            time.sleep(wait_sec)
            continue
        break
    LOGGER.warning("Weather request failed | status=%s | url=%s", last_status, url)
    return None


def _daily_tmax_for_date(payload, target_date):
    if not payload or "daily" not in payload:
        return None
    dates = payload["daily"].get("time", [])
    values = payload["daily"].get("temperature_2m_max", [])
    target_iso = target_date.isoformat()
    if target_iso not in dates:
        return None
    index = dates.index(target_iso)
    if index >= len(values) or values[index] is None:
        return None
    return float(values[index])


def fetch_open_meteo_tmax(model_name, target_date):
    params = {
        "latitude": FORECAST_LATITUDE,
        "longitude": FORECAST_LONGITUDE,
        "daily": "temperature_2m_max",
        "timezone": "Asia/Seoul",
        "forecast_days": 7,
        "past_days": 1,
        "models": model_name,
    }
    payload = _request_json(OPEN_METEO_FORECAST_URL, params)
    time.sleep(REQUEST_GAP_SEC)
    value = _daily_tmax_for_date(payload, target_date)
    if value is None:
        return None
    return round(value, 2)


def fetch_openweather_tmax(target_date):
    if not OPENWEATHER_API_KEY:
        return None
    params = {
        "lat": FORECAST_LATITUDE,
        "lon": FORECAST_LONGITUDE,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
        "exclude": "minutely,hourly,alerts",
    }
    payload = _request_json("https://api.openweathermap.org/data/3.0/onecall", params)
    time.sleep(REQUEST_GAP_SEC)
    if not payload:
        return None
    for day in payload.get("daily", []):
        day_dt = datetime.fromtimestamp(day["dt"], tz=timezone.utc).date()
        if day_dt == target_date:
            return round(float(day["temp"]["max"]), 2)
    return None


def fetch_meteoblue_tmax(target_date):
    if not METEOBLUE_API_KEY:
        return None
    params = {
        "apikey": METEOBLUE_API_KEY,
        "lat": FORECAST_LATITUDE,
        "lon": FORECAST_LONGITUDE,
        "asl": 7,
        "format": "json",
    }
    payload = _request_json(
        "https://my.meteoblue.com/packages/basic-1h_basic-day",
        params,
    )
    time.sleep(REQUEST_GAP_SEC)
    if not payload:
        return None
    data_day = payload.get("data_day", {})
    times = data_day.get("time", [])
    temps = data_day.get("temperature_max", [])
    target_iso = target_date.isoformat()
    if target_iso not in times:
        return None
    index = times.index(target_iso)
    if index >= len(temps) or temps[index] is None:
        return None
    return round(float(temps[index]), 2)


def fetch_historical_model_tmax_range(model_name, start_date, end_date):
    params = {
        "latitude": FORECAST_LATITUDE,
        "longitude": FORECAST_LONGITUDE,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "temperature_2m_max",
        "timezone": "Asia/Seoul",
        "models": model_name,
    }
    payload = _request_json(OPEN_METEO_HISTORICAL_FORECAST_URL, params)
    time.sleep(REQUEST_GAP_SEC)
    if not payload:
        return {}
    dates = payload.get("daily", {}).get("time", [])
    values = payload.get("daily", {}).get("temperature_2m_max", [])
    return {
        day: float(value)
        for day, value in zip(dates, values)
        if value is not None
    }


def _load_calibration_cache():
    if not os.path.exists(FORECAST_CALIBRATION_CACHE_FILE):
        return None
    try:
        with open(FORECAST_CALIBRATION_CACHE_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _save_calibration_cache(payload):
    with open(FORECAST_CALIBRATION_CACHE_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def compute_model_calibration(force_refresh=False):
    cached = _load_calibration_cache()
    if cached and not force_refresh:
        age_hours = (
            datetime.now(timezone.utc) - datetime.fromisoformat(cached["computed_at"])
        ).total_seconds() / 3600
        if age_hours < 12 and cached.get("models"):
            return cached

    actuals = get_rksi_actuals(force_refresh=force_refresh)
    if not actuals:
        return cached or {"models": {}, "residual_std": 0.0, "ground_truth": "wunderground_rksi"}

    end_date = date.today() - timedelta(days=2)
    start_date = end_date - timedelta(days=FORECAST_BIAS_LOOKBACK_DAYS)
    calibration_days = sorted(
        day
        for day in actuals
        if start_date.isoformat() <= day <= end_date.isoformat()
    )

    model_stats = {}
    residual_errors = []

    for model_key, model_name in OPEN_METEO_MODELS.items():
        hindcasts = fetch_historical_model_tmax_range(model_name, start_date, end_date)
        errors = []
        for day in calibration_days:
            actual = actuals.get(day)
            forecast = hindcasts.get(day)
            if actual is None or forecast is None:
                continue
            errors.append(float(actual) - float(forecast))

        if not errors:
            continue

        error_array = np.array(errors, dtype=float)
        mae = float(np.mean(np.abs(error_array)))
        bias = float(np.mean(error_array))
        error_variance = float(np.var(error_array))
        model_stats[model_key] = {
            "bias": round(bias, 4),
            "mae": round(mae, 4),
            "error_variance": round(error_variance, 4),
            "samples": len(errors),
        }
        residual_errors.extend(errors)

    residual_std = float(np.std(residual_errors)) if residual_errors else 0.0
    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth": "wunderground_rksi",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "models": model_stats,
        "residual_std": round(residual_std, 4),
        "calibration_days": len(calibration_days),
    }
    _save_calibration_cache(payload)
    return payload


def get_model_calibration():
    calibration = compute_model_calibration()
    return calibration.get("models", {}), float(calibration.get("residual_std", 0.0))


def _model_stats(model_key, model_calibration):
    stats = model_calibration.get(model_key)
    if stats:
        return {
            "bias": float(stats.get("bias", 0.0)),
            "mae": float(stats.get("mae", DEFAULT_UNCALIBRATED_MAE)),
            "error_variance": float(stats.get("error_variance", stats.get("mae", DEFAULT_UNCALIBRATED_MAE))),
            "samples": int(stats.get("samples", 0)),
        }
    return {
        "bias": 0.0,
        "mae": DEFAULT_UNCALIBRATED_MAE,
        "error_variance": DEFAULT_UNCALIBRATED_MAE,
        "samples": 0,
    }


def apply_per_model_correction(raw_forecasts, model_calibration):
    corrected = {}
    biases = {}
    for model_key, raw_value in raw_forecasts.items():
        bias = float(_model_stats(model_key, model_calibration)["bias"])
        biases[model_key] = round(bias, 4)
        corrected[model_key] = round(float(raw_value) + bias, 2)
    return corrected, biases


def _performance_weights(model_keys, model_calibration, min_mae=0.25):
    inverse = {
        model_key: 1.0 / max(float(_model_stats(model_key, model_calibration)["mae"]), min_mae)
        for model_key in model_keys
    }
    total = sum(inverse.values())
    if total <= 0:
        equal = 1.0 / len(model_keys)
        return {model_key: equal for model_key in model_keys}
    return {model_key: value / total for model_key, value in inverse.items()}


def _stability_factors(model_keys, model_calibration):
    factors = {}
    for model_key in model_keys:
        variance = float(_model_stats(model_key, model_calibration)["error_variance"])
        penalty = variance / max(FORECAST_STABILITY_VARIANCE_SCALE, 0.1)
        factors[model_key] = float(np.exp(-penalty))
    return factors


def _soft_outlier_damping(corrected_forecasts):
    values = np.array(list(corrected_forecasts.values()), dtype=float)
    median = float(np.median(values))
    tau = max(FORECAST_SOFT_OUTLIER_TAU, 0.1) ** 2
    damping = {}
    for model_key, value in corrected_forecasts.items():
        distance = abs(float(value) - median)
        damping[model_key] = float(np.exp(-(distance ** 2) / tau))
    return damping, median


def _cap_model_weights(raw_weights):
    if not raw_weights:
        return {}

    total = sum(raw_weights.values())
    if total <= 0:
        equal = 1.0 / len(raw_weights)
        return {key: equal for key in raw_weights}

    weights = {key: value / total for key, value in raw_weights.items()}
    capped = dict(weights)
    for _ in range(len(weights)):
        overflow = {key: weight - FORECAST_MAX_MODEL_WEIGHT for key, weight in capped.items() if weight > FORECAST_MAX_MODEL_WEIGHT}
        if not overflow:
            break
        overflow_total = sum(overflow.values())
        for key in overflow:
            capped[key] = FORECAST_MAX_MODEL_WEIGHT
        recipients = [key for key in capped if key not in overflow]
        if not recipients:
            equal = 1.0 / len(capped)
            return {key: equal for key in capped}
        recipient_total = sum(capped[key] for key in recipients)
        if recipient_total <= 0:
            bump = overflow_total / len(recipients)
            for key in recipients:
                capped[key] += bump
        else:
            for key in recipients:
                capped[key] += overflow_total * (capped[key] / recipient_total)

    final_total = sum(capped.values())
    if final_total <= 0:
        return weights
    return {key: value / final_total for key, value in capped.items()}


def build_soft_ensemble(corrected_forecasts, model_calibration):
    if not corrected_forecasts:
        return None

    model_keys = list(corrected_forecasts.keys())
    performance = _performance_weights(model_keys, model_calibration)
    stability = _stability_factors(model_keys, model_calibration)
    damping, median = _soft_outlier_damping(corrected_forecasts)

    raw_weights = {
        model_key: (
            performance[model_key]
            * stability[model_key]
            * damping[model_key]
        )
        for model_key in model_keys
    }
    capped_weights = _cap_model_weights(raw_weights)
    final_weights = capped_weights

    values = np.array([corrected_forecasts[key] for key in model_keys], dtype=float)
    weights = np.array([final_weights[key] for key in model_keys], dtype=float)
    mean = float(np.dot(weights, values))
    if len(values) > 1:
        variance = float(np.average((values - mean) ** 2, weights=weights))
        ensemble_std = float(np.sqrt(variance))
    else:
        variance = 0.0
        ensemble_std = 0.0

    return {
        "mean": mean,
        "variance": variance,
        "ensemble_std": ensemble_std,
        "weights": {key: round(final_weights[key], 4) for key in model_keys},
        "median": median,
        "damping": {key: round(damping[key], 4) for key in model_keys},
    }


def evaluate_risk_flags(model_count, ensemble_std, model_weights):
    flags = []
    if model_count < FORECAST_MIN_MODELS:
        flags.append(f"insufficient_models ({model_count} < {FORECAST_MIN_MODELS})")

    if ensemble_std > FORECAST_MAX_STD_DEV:
        flags.append(f"extreme_ensemble_variance ({ensemble_std:.2f} > {FORECAST_MAX_STD_DEV})")

    if model_weights and ensemble_std > 2.0:
        dominant_weight = max(model_weights.values())
        if dominant_weight < FORECAST_MIN_DOMINANT_WEIGHT:
            flags.append(
                f"no_stable_agreement (dominant_weight={dominant_weight:.2f} "
                f"< {FORECAST_MIN_DOMINANT_WEIGHT})"
            )

    return flags


def collect_model_forecasts(target_date):
    forecasts = {}
    for model_key, model_name in OPEN_METEO_MODELS.items():
        value = fetch_open_meteo_tmax(model_name, target_date)
        if value is not None:
            forecasts[model_key] = value

    openweather = fetch_openweather_tmax(target_date)
    if openweather is not None:
        forecasts["openweather"] = openweather

    meteoblue = fetch_meteoblue_tmax(target_date)
    if meteoblue is not None:
        forecasts["meteoblue"] = meteoblue

    return forecasts


def _bucket_label(market_range):
    temp = market_range["temp"]
    market_type = market_range.get("type", "exact")
    if market_type == "below_or_equal":
        return f"{temp}C_or_below"
    if market_type == "above_or_equal":
        return f"{temp}C_or_higher"
    return f"{temp}C"


def build_temperature_distribution(market_ranges, mean, std):
    """Discrete bucket probabilities for strategy output."""
    distribution = {}
    for market_range in market_ranges:
        if market_range.get("closed"):
            continue
        label = _bucket_label(market_range)
        prob = float(probability_of_market(market_range, mean, std))
        distribution[label] = round(prob, 4)
    return distribution


def compute_forecast_confidence(model_count, ensemble_std, model_weights):
    """0–1 confidence score from model agreement and count."""
    if model_count <= 0:
        return 0.0
    count_factor = min(1.0, model_count / 4.0)
    variance_penalty = max(0.0, 1.0 - ensemble_std / FORECAST_MAX_STD_DEV)
    weight_entropy = 0.0
    if model_weights:
        dominant = max(model_weights.values())
        weight_entropy = dominant
    return round(min(1.0, count_factor * 0.4 + variance_penalty * 0.4 + weight_entropy * 0.2), 4)


def build_probability_distribution(market_ranges, mean, std):
    distribution = {}
    for market_range in market_ranges:
        if market_range.get("closed"):
            continue
        label = _bucket_label(market_range)
        yes_prob = float(probability_of_market(market_range, mean, std))
        no_prob = 1.0 - yes_prob
        market_no_price = float(market_range.get("no_price", 1 - market_range.get("yes_price", 0.5)))
        distribution[label] = {
            "yes_probability": round(yes_prob, 4),
            "no_probability": round(no_prob, 4),
            "market_no_price": round(market_no_price, 4),
            "edge_no": round(no_prob - market_no_price, 4),
        }
    return distribution


def _best_recommendation(probability_distribution):
    best = None
    for label, stats in probability_distribution.items():
        edge_no = stats["edge_no"]
        if edge_no <= 0:
            continue
        if best is None or edge_no > best["edge"]:
            best = {
                "direction": f"NO {label}",
                "confidence": round(stats["no_probability"], 4),
                "edge": round(edge_no, 4),
            }
    return best


def build_forecast(target_date, market_ranges=None):
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    raw_forecasts = collect_model_forecasts(target_date)
    model_calibration, residual_std = get_model_calibration()
    market_delta, _market_calibration = get_market_delta()

    result = ForecastResult(
        target_date=target_date.isoformat(),
        forecast_models=raw_forecasts,
        market_delta=market_delta,
        tradeable=False,
    )

    if not raw_forecasts:
        result.risk_flags.append("no_model_outputs")
        return result

    corrected_forecasts, model_biases = apply_per_model_correction(raw_forecasts, model_calibration)
    ensemble = build_soft_ensemble(corrected_forecasts, model_calibration)
    if ensemble is None:
        result.risk_flags.append("ensemble_failed")
        return result

    error_buffer = max(FORECAST_HISTORICAL_ERROR_BUFFER, residual_std)
    std_dev = float(np.sqrt(ensemble["variance"]) + error_buffer)
    rksi_forecast = round(ensemble["mean"], 2)
    market_adjusted = round(rksi_forecast + market_delta, 2)

    result.model_biases = model_biases
    result.corrected_models = corrected_forecasts
    result.model_weights = ensemble["weights"]
    result.rksi_forecast = rksi_forecast
    result.market_adjusted_forecast = market_adjusted
    result.variance = round(ensemble["variance"], 4)
    result.std_dev = round(std_dev, 4)
    result.mean = market_adjusted
    result.std = result.std_dev

    result.risk_flags = evaluate_risk_flags(
        len(raw_forecasts),
        ensemble["ensemble_std"],
        ensemble["weights"],
    )
    result.tradeable = len(result.risk_flags) == 0

    if market_ranges:
        result.temperature_distribution = build_temperature_distribution(
            market_ranges,
            result.mean,
            result.std,
        )
        result.expected_value = result.mean
        result.confidence = compute_forecast_confidence(
            len(raw_forecasts),
            ensemble["ensemble_std"],
            ensemble["weights"],
        )
        result.probability_distribution = build_probability_distribution(
            market_ranges,
            result.mean,
            result.std,
        )
        result.recommended_trade = _best_recommendation(result.probability_distribution)

    return result


def get_forecast(city):
    del city
    today = date.today()
    result = build_forecast(today)
    if result.market_adjusted_forecast is None:
        return []
    center = result.market_adjusted_forecast
    spread = result.std_dev or 1.0
    return [center - spread, center, center + spread]
