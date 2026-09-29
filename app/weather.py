"""Cliente da WeatherAPI (https://www.weatherapi.com/docs/).

- dia passado  -> history.json
- hoje/futuro  -> forecast.json (o plano actual devolve no maximo 3 dias)

As condicoes vem na lingua pedida (pt; sem "lang" a API responde em ingles).
A cache e por provincia, dia e lingua; cada resposta guarda todos os dias que devolve.
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


def _call(endpoint: str, params: dict, lang: str) -> dict:
    if lang != "en":
        params = {**params, "lang": lang}
    query = urllib.parse.urlencode({"key": config.API_WEATHER_KEY, **params})
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


def _cached(q: str, day: date, lang: str) -> dict | None:
    with _cache_lock:
        hit = _cache.get(f"{q}|{day.isoformat()}|{lang}")
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    return None


def _remember(q: str, lang: str, body: dict, today: date) -> dict[str, dict]:
    """Extrai e guarda na cache todos os dias da resposta: {"AAAA-MM-DD": dados}."""
    days = {}
    for fd in body.get("forecast", {}).get("forecastday", []):
        d = fd["day"]
        days[fd["date"]] = {
            "max": d["maxtemp_c"],
            "min": d["mintemp_c"],
            "avg": d["avgtemp_c"],
            "condition": d.get("condition", {}).get("text", ""),
            "current": body.get("current", {}).get("temp_c") if fd["date"] == today.isoformat() else None,
        }
    now = time.monotonic()
    with _cache_lock:
        for iso, data in days.items():
            _cache[f"{q}|{iso}|{lang}"] = (now, data)
    return days


def get_day_weather(q: str, day: date, today: date, lang: str) -> dict:
    hit = _cached(q, day, lang)
    if hit is not None:
        log.info("[cache] %s %s %s -> %s", q, day.isoformat(), lang, hit)
        return hit

    diff_days = (day - today).days
    if diff_days >= MAX_FORECAST_DAYS:
        raise WeatherUnavailable("fora do alcance da previsao")

    if diff_days < 0:
        body = _call("history.json", {"q": q, "dt": day.isoformat()}, lang)
    else:
        body = _call("forecast.json", {"q": q, "days": diff_days + 1, "aqi": "no", "alerts": "no"}, lang)

    data = _remember(q, lang, body, today).get(day.isoformat())
    if data is None:
        raise WeatherUnavailable("data nao devolvida pela API")
    log.info("[data] %s %s %s -> %s", q, day.isoformat(), lang, data)
    return data


def get_forecast(q: str, today: date, days: int, lang: str) -> list[tuple[date, dict]]:
    """Previsao de hoje e dos dias seguintes, numa so chamada. So devolve os dias que a API enviou."""
    wanted = [today + timedelta(days=i) for i in range(days)]
    hits = [_cached(q, d, lang) for d in wanted]
    if all(h is not None for h in hits):
        log.info("[cache] %s previsao %d dias %s", q, days, lang)
        return list(zip(wanted, hits))

    body = _call("forecast.json", {"q": q, "days": days, "aqi": "no", "alerts": "no"}, lang)
    got = _remember(q, lang, body, today)
    result = [(d, got[d.isoformat()]) for d in wanted if d.isoformat() in got]
    if not result:
        raise WeatherUnavailable("previsao nao devolvida pela API")
    log.info("[data] %s previsao %s -> %s", q, lang, result)
    return result
