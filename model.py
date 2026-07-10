import numpy as np
from scipy.stats import norm


def estimate_distribution(temps):
    mean = np.mean(temps)
    std = np.std(temps) + 0.5
    return mean, std


def distribution_from_forecast(final_temperature, ensemble_std, historical_error_buffer=0.3):
    std = float(ensemble_std) + float(historical_error_buffer)
    return float(final_temperature), max(std, 0.25)


def probability_of_exact_temp(temp, mean, std):
    lower = temp - 0.5
    upper = temp + 0.5

    prob = norm.cdf(upper, mean, std) - norm.cdf(lower, mean, std)
    return prob


def probability_of_market(market_range, mean, std):
    temp = market_range["temp"]
    market_type = market_range.get("type", "exact")

    if market_type == "below_or_equal":
        return norm.cdf(temp + 0.5, mean, std)

    if market_type == "above_or_equal":
        return 1 - norm.cdf(temp - 0.5, mean, std)

    return probability_of_exact_temp(temp, mean, std)
