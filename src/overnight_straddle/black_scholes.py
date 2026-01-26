from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class OptionQuote:
    call: float
    put: float


def black_scholes_quote(
    *,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    vol: float,
) -> OptionQuote:
    """
    Black–Scholes European option prices (no dividends).

    Returns call/put mid prices. For extremely small T or vol, falls back to
    intrinsic value.
    """
    s = float(spot)
    k = float(strike)
    t = float(time_to_expiry_years)
    r = float(risk_free_rate)
    sigma = float(vol)

    if s <= 0.0 or k <= 0.0:
        raise ValueError("spot and strike must be positive")

    if t <= 0.0 or sigma <= 0.0:
        call = max(s - k, 0.0)
        put = max(k - s, 0.0)
        return OptionQuote(call=call, put=put)

    vsqrt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vsqrt
    d2 = d1 - vsqrt

    disc = math.exp(-r * t)
    call = s * _norm_cdf(d1) - k * disc * _norm_cdf(d2)
    put = k * disc * _norm_cdf(-d2) - s * _norm_cdf(-d1)
    return OptionQuote(call=call, put=put)

