"""Cliente da WeatherAPI (https://www.weatherapi.com/docs/).

- dia passado  -> history.json
- hoje/futuro  -> forecast.json (o plano actual devolve no maximo 3 dias)
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import config

TIMEOUT_S = 8  # bem abaixo do deadline de ~15s do Gateway (spec 4.3)
CACHE_TTL_S = 10 * 60
MAX_FORECAST_DAYS = 14

log = logging.getLogger("weather")

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def available_range(today: date) -> tuple[date, date]:
    """Primeiro e ultimo dia com dados no plano actual."""
    return (today - timedelta(days=config.WEATHER_HISTORY_DAYS),
            today + timedelta(days=config.WEATHER_FORECAST_DAYS))


class WeatherUnavailable(Exception):
    """A API nao tem dados para a data pedida (limite do plano ou fora de alcance)."""


def _call(endpoint: str, params: dict) -> dict:
    query = urllib.parse.urlencode({"key": config.API_WEATHER_KEY, "lang": "pt", **params})
    url = f"{config.API_WEATHER_URL}{endpoint}?{query}"
    t0 = time.monotonic()
    status = None
    try:
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as res:
                status = res.status
                body = json.load(res)
        except urllib.error.HTTPError as err:
            status = err.code
            try:
                body = json.load(err)
            except ValueError:
                raise RuntimeError(f"WeatherAPI HTTP {err.code}") from err
    except Exception as err:
        log.error("[call] %s params=%s status=%s time=%dms erro=%s",
                  endpoint, params, status, (time.monotonic() - t0) * 1000, err)
        raise

    error = body.get("error")
    log.info("[call] %s params=%s status=%s time=%dms erro=%s",
             endpoint, params, status, (time.monotonic() - t0) * 1000, error)
    if error:
        # 1008/2009: plano sem acesso a essa data/recurso
        if error.get("code") in (1008, 2009):
            raise WeatherUnavailable(error.get("message"))
        raise RuntimeError(f"WeatherAPI {error.get('code')}: {error.get('message')}")
    return body


def get_day_weather(q: str, day: date, today: date) -> dict:
    key = f"{q}|{day.isoformat()}"
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        log.info("[cache] %s %s -> %s", q, day.isoformat(), hit[1])
        return hit[1]

    diff_days = (day - today).days
    if diff_days >= MAX_FORECAST_DAYS:
        raise WeatherUnavailable("fora do alcance da previsao")

    if diff_days < 0:
        body = _call("history.json", {"q": q, "dt": day.isoformat()})
    else:
        body = _call("forecast.json", {"q": q, "days": diff_days + 1, "aqi": "no", "alerts": "no"})

    forecast_days = body.get("forecast", {}).get("forecastday", [])
    fd = next((f for f in forecast_days if f.get("date") == day.isoformat()), None)
    if fd is None:
        raise WeatherUnavailable("data nao devolvida pela API")

    d = fd["day"]
    data = {
        "max": d["maxtemp_c"],
        "min": d["mintemp_c"],
        "avg": d["avgtemp_c"],
        "condition": d.get("condition", {}).get("text", ""),
        "current": body.get("current", {}).get("temp_c") if diff_days == 0 else None,
    }
    log.info("[data] %s %s -> %s", q, day.isoformat(), data)
    with _cache_lock:
        _cache[key] = (time.monotonic(), data)
    return data
