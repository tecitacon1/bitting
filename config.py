import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(Path(__file__).resolve().parent / ".env")


def _env_bool(name, default="false"):
    return os.getenv(name, default).lower() == "true"


def _env_float(name, default):
    return float(os.getenv(name, str(default)))


def _env_int(name, default):
    return int(os.getenv(name, str(default)))


CITY = os.getenv("CITY", "Seoul")
CHECK_TODAY = _env_bool("CHECK_TODAY", "true")
MARKET_DAYS_AHEAD = _env_int("MARKET_DAYS_AHEAD", 2)

# Forecast (Incheon Airport / RKSI)
FORECAST_LATITUDE = _env_float("FORECAST_LATITUDE", 37.4602)
FORECAST_LONGITUDE = _env_float("FORECAST_LONGITUDE", 126.4407)
FORECAST_LOCATION_NAME = os.getenv("FORECAST_LOCATION_NAME", "Incheon Airport (RKSI)")
FORECAST_MAX_STD_DEV = _env_float("FORECAST_MAX_STD_DEV", 3.5)
FORECAST_MIN_MODELS = _env_int("FORECAST_MIN_MODELS", 2)
FORECAST_HISTORICAL_ERROR_BUFFER = _env_float("FORECAST_HISTORICAL_ERROR_BUFFER", 0.3)
FORECAST_BIAS_LOOKBACK_DAYS = _env_int("FORECAST_BIAS_LOOKBACK_DAYS", 30)
FORECAST_SOFT_OUTLIER_TAU = _env_float("FORECAST_SOFT_OUTLIER_TAU", 2.0)
FORECAST_STABILITY_VARIANCE_SCALE = _env_float("FORECAST_STABILITY_VARIANCE_SCALE", 2.0)
FORECAST_MIN_DOMINANT_WEIGHT = _env_float("FORECAST_MIN_DOMINANT_WEIGHT", 0.2)
FORECAST_MAX_MODEL_WEIGHT = _env_float("FORECAST_MAX_MODEL_WEIGHT", 0.45)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
METEOBLUE_API_KEY = os.getenv("METEOBLUE_API_KEY", "")
WUNDERGROUND_API_KEY = os.getenv("WUNDERGROUND_API_KEY", "")
WUNDERGROUND_STATION_ID = os.getenv("WUNDERGROUND_STATION_ID", "RKSI:9:KR")
RKSI_ACTUALS_CACHE_FILE = os.getenv("RKSI_ACTUALS_CACHE_FILE", "rksi_actuals.json")
FORECAST_CALIBRATION_CACHE_FILE = os.getenv(
    "FORECAST_CALIBRATION_CACHE_FILE",
    "forecast_calibration.json",
)
MARKET_CALIBRATION_CACHE_FILE = os.getenv(
    "MARKET_CALIBRATION_CACHE_FILE",
    "market_calibration.json",
)
MARKET_CALIBRATION_LOOKBACK_DAYS = _env_int("MARKET_CALIBRATION_LOOKBACK_DAYS", 60)

# Live ladder trader (Railway worker)
LIVE_TRADING_ENABLED = _env_bool("LIVE_TRADING_ENABLED", "false")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
POLYMARKET_SIGNATURE_TYPE = _env_int("POLYMARKET_SIGNATURE_TYPE", 3)
POLYMARKET_CLOB_HOST = os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID = _env_int("POLYMARKET_CHAIN_ID", 137)
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")

LADDER_POLL_INTERVAL_SEC = _env_int("LADDER_POLL_INTERVAL_SEC", 45)
LADDER_ORDER_USDC = _env_float("LADDER_ORDER_USDC", 10.0)
LADDER_MIN_ORDER_USDC = _env_float("LADDER_MIN_ORDER_USDC", 5.0)
LADDER_MAX_BUY_PRICE = _env_float("LADDER_MAX_BUY_PRICE", 0.85)
LADDER_ENTRY_FLOOR_OFFSET = _env_int("LADDER_ENTRY_FLOOR_OFFSET", 1)
LADDER_PEAK_STALL_MINUTES = _env_int("LADDER_PEAK_STALL_MINUTES", 45)
LADDER_TRADE_START_HOUR_KST = _env_int("LADDER_TRADE_START_HOUR_KST", 10)
LADDER_TRADE_END_HOUR_KST = _env_int("LADDER_TRADE_END_HOUR_KST", 18)
LADDER_STATE_FILE = os.getenv("LADDER_STATE_FILE", "ladder_state.json")
