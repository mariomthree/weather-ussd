"""Maquina de estados dos menus USSD.

A navegacao usa uma pilha de ecras: "0" volta ao ecra anterior, "00" ao menu principal.
A pilha e guardada por MSISDN (ver store.py); se a sessao anterior foi interrompida,
ao marcar de novo o assinante pode retomar no ecra onde estava.

Os textos existem em portugues e ingles (TEXTS). A lingua vem de State.lang, escolhida
no menu "Idioma/Language" e guardada por MSISDN.
"""
import logging
import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import config
from .provinces import PROVINCES
from .store import State
from .weather import WeatherUnavailable, available_range, get_day_weather, get_forecast

log = logging.getLogger(__name__)

MAX_LEN = 160  # spec 4.2: maximo 160 caracteres ASCII
TZ = ZoneInfo("Africa/Maputo")
# Previsao num so ecra: no maximo 3 dias, para caber nos 160 caracteres.
FORECAST_DAYS = min(3, config.WEATHER_FORECAST_DAYS + 1)

TEXTS = {
    "pt": {
        "main": "Estado do Tempo\n\n1. Tempo hoje\n2. Previsao {n} dias\n3. Outra data\n4. Idioma/Language\n0. Sair",
        "today": "Tempo hoje",
        "forecast": "Previsao {n} dias",
        "on_date": "Tempo de {date}",
        "ask_date": "Outra data\n\nIntroduza a data (DDMMYYYY)\nDisponivel: {first} a {last}",
        "choose_province": "Escolha a provincia:",
        "next": "Proximo",
        "nav": "0. Voltar\n00. Menu Principal",
        "resume": "Deseja retomar a sua sessao anterior?\n\n1. Sim\n2. Nao",
        "lang_changed": "Idioma alterado.",
        "bye": "Obrigado por usar o servico.",
        "invalid_option": "Opcao invalida.",
        "invalid_date": "Data invalida.",
        "out_of_range": "Data fora do intervalo.",
        "no_data": "Sem dados para esta data.",
        "unavailable": "Servico indisponivel. Tente mais tarde.",
        "conditions": "Condicoes",
        "today_label": "Hoje",
        "months": ["Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
                   "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"],
        "weekdays": ["Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado", "Domingo"],
    },
    "en": {
        "main": "Weather\n\n1. Today's weather\n2. {n}-day forecast\n3. Other date\n4. Idioma/Language\n0. Exit",
        "today": "Today's weather",
        "forecast": "{n}-day forecast",
        "on_date": "Weather on {date}",
        "ask_date": "Other date\n\nEnter the date (DDMMYYYY)\nAvailable: {first} to {last}",
        "choose_province": "Choose the province:",
        "next": "Next",
        "nav": "0. Back\n00. Main Menu",
        "resume": "Do you want to resume your previous session?\n\n1. Yes\n2. No",
        "lang_changed": "Language changed.",
        "bye": "Thank you for using the service.",
        "invalid_option": "Invalid option.",
        "invalid_date": "Invalid date.",
        "out_of_range": "Date out of range.",
        "no_data": "No data for this date.",
        "unavailable": "Service unavailable. Try again later.",
        "conditions": "Conditions",
        "today_label": "Today",
        "months": ["January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"],
        "weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    },
}

# Sempre nas duas linguas, para quem caiu na lingua errada conseguir mudar.
LANG_SCREEN = "Idioma / Language\n\n1. Portugues\n2. English\n0. Voltar/Back"
LANG_OPTIONS = {"1": "pt", "2": "en"}


def tx(s: State) -> dict:
    return TEXTS.get(s.lang, TEXTS["pt"])


def ascii_text(text: str) -> str:
    """Remove acentos e caracteres fora do ASCII imprimivel (a API devolve "Céu limpo", etc.)."""
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if c == "\n" or 0x20 <= ord(c) <= 0x7E)


def reply(message: str, end_session: bool = False) -> dict:
    return {"message": ascii_text(message)[:MAX_LEN], "end_session": end_session}


def today() -> date:
    return datetime.now(TZ).date()


def parse_date(value: str) -> date | None:
    """'DDMMYYYY' -> date, ou None se a data for invalida."""
    if not re.fullmatch(r"\d{8}", value):
        return None
    try:
        return datetime.strptime(value, "%d%m%Y").date()
    except ValueError:
        return None


def full_date(s: State, d: date) -> str:
    """28-Setembro-2026 / 28-September-2026"""
    return f"{d.day:02d}-{tx(s)['months'][d.month - 1]}-{d.year}"


def province_name(s: State, province: dict) -> str:
    return province.get(f"name_{s.lang}", province["name"])


def fmt(n: float) -> str:
    return f"{round(n)}C"


# Cada ecra: titulo, linha em branco, opcoes.
def screen_main(s: State, notice: str) -> str:
    return tx(s)["main"].format(n=FORECAST_DAYS)


PAGE_SIZE = 6
NEXT = str(PAGE_SIZE + 1)  # opcao "7.Proximo"

# Paginas de 6 provincias; em cada pagina as opcoes sao renumeradas a partir de 1.
PAGES = [PROVINCES[i:i + PAGE_SIZE] for i in range(0, len(PROVINCES), PAGE_SIZE)]


def province_page(state: str) -> int | None:
    """'PROVINCE:<n>' -> n (cada pagina e um ecra na pilha, por isso "0" volta a pagina anterior)."""
    if state == "PROVINCE":  # formato antigo guardado na BD
        return 0
    if state.startswith("PROVINCE:"):
        return int(state.split(":", 1)[1])
    return None


def province_title(s: State) -> str:
    t = tx(s)
    if s.data.get("mode") == "forecast":
        return t["forecast"].format(n=FORECAST_DAYS)
    d = date.fromisoformat(s.data["date"])
    return t["today"] if d == today() else t["on_date"].format(date=full_date(s, d))


def screen_province(s: State, notice: str, page: int) -> str:
    # "0" (voltar) e "00" (menu) funcionam aqui, mas nao sao mostrados para simplificar a lista.
    t = tx(s)
    title = notice or province_title(s)
    lines = [f"{title}\n\n{t['choose_province']}",
             *(f"{n}. {province_name(s, p)}" for n, p in enumerate(PAGES[page], 1))]
    if page < len(PAGES) - 1:
        lines.append(f"\n{NEXT}. {t['next']}")
    return "\n".join(lines)


def screen_ask_date(s: State, notice: str) -> str:
    first, last = available_range(today())
    t = tx(s)
    return f"{t['ask_date'].format(first=f'{first:%d/%m}', last=f'{last:%d/%m}')}\n\n{t['nav']}"


def screen_resume(s: State, notice: str) -> str:
    return tx(s)["resume"]


def screen_lang(s: State, notice: str) -> str:
    return LANG_SCREEN


SCREENS = {
    "RESUME": screen_resume,
    "MAIN": screen_main,
    "ASK_DATE": screen_ask_date,
    "LANG": screen_lang,
}


def render(s: State, state: str, notice: str = "") -> str:
    page = province_page(state)
    if page is not None:
        return screen_province(s, notice, page)
    text = SCREENS[state](s, notice)
    return f"{notice}\n{text}" if notice else text


def show(s: State, state: str, notice: str = "") -> dict:
    if not s.stack or s.stack[-1] != state:
        s.stack.append(state)
    return reply(render(s, state, notice))


def go_back(s: State) -> dict:
    s.stack.pop()
    return reply(render(s, s.stack[-1]))


# Ecras finais: sao mostrados com end_session=true, por isso nao levam opcoes de navegacao.
def build_result(s: State, province: dict, day: date) -> str:
    t = tx(s)
    header = f"{province_name(s, province)}\n{full_date(s, day)}\n\n"
    try:
        w = get_day_weather(province["q"], day, today(), s.lang)
    except WeatherUnavailable:
        return f"{header}{t['no_data']}"
    except Exception as err:  # rede, timeout, resposta inesperada
        log.error("[weather] %s %s: %s", province["name"], day, err)
        return f"{header}{t['unavailable']}"

    return f"{header}Max: {fmt(w['max'])}\nMin: {fmt(w['min'])}\n{t['conditions']}: {w['condition']}"


def build_forecast(s: State, province: dict) -> str:
    """Um dia por linha, sem condicoes, para caber nos 160 caracteres."""
    t = tx(s)
    header = f"{province_name(s, province)}\n{t['forecast'].format(n=FORECAST_DAYS)}\n\n"
    day0 = today()
    try:
        days = get_forecast(province["q"], day0, FORECAST_DAYS, s.lang)
    except WeatherUnavailable:
        return f"{header}{t['no_data']}"
    except Exception as err:
        log.error("[weather] %s previsao: %s", province["name"], err)
        return f"{header}{t['unavailable']}"

    labels = [t["today_label"] if d == day0 else t["weekdays"][d.weekday()] for d, _ in days]
    width = max(map(len, labels))
    lines = [f"{label:<{width}} {d:%d/%m} Max {fmt(w['max'])} Min {fmt(w['min'])}"
             for label, (d, w) in zip(labels, days)]
    return header + "\n".join(lines)


def handle(s: State, raw_input: str | None, is_new: bool) -> dict:
    value = (raw_input or "").strip()
    if is_new:
        # Interaccao anterior interrompida para alem do menu principal -> oferecer retoma.
        known = all(st in SCREENS or province_page(st) is not None for st in s.stack)
        if known and len(s.stack) > 1 and s.stack[0] == "MAIN":
            s.data["resume_stack"] = s.stack
            s.stack = []
            return show(s, "RESUME")
        s.stack, s.data = [], {}
        return show(s, "MAIN")

    if not s.stack:  # estado perdido/expirado a meio da sessao
        return show(s, "MAIN")

    state = s.stack[-1]
    t = tx(s)

    if state == "RESUME":
        if value == "1":
            s.stack = s.data.pop("resume_stack")
            return reply(render(s, s.stack[-1]))
        if value in ("2", "00"):
            s.stack, s.data = [], {}
            return show(s, "MAIN")
        return show(s, "RESUME", t["invalid_option"])

    if state != "MAIN":
        if value == "00":
            s.stack = []
            return show(s, "MAIN")
        if value == "0":
            return go_back(s)

    if state == "MAIN":
        if value == "1":
            s.data.update(mode="day", date=today().isoformat())
            return show(s, "PROVINCE:0")
        if value == "2":
            s.data["mode"] = "forecast"
            return show(s, "PROVINCE:0")
        if value == "3":
            return show(s, "ASK_DATE")
        if value == "4":
            return show(s, "LANG")
        if value == "0":
            return reply(t["bye"], end_session=True)
        return show(s, "MAIN", t["invalid_option"])

    if state == "LANG":
        lang = LANG_OPTIONS.get(value)
        if lang is None:
            return show(s, "LANG", t["invalid_option"])
        s.lang = lang
        s.stack = []
        return show(s, "MAIN", tx(s)["lang_changed"])

    if state == "ASK_DATE":
        day = parse_date(value)
        if day is None:
            return show(s, "ASK_DATE", t["invalid_date"])
        first, last = available_range(today())
        if not first <= day <= last:
            return show(s, "ASK_DATE", t["out_of_range"])
        s.data.update(mode="day", date=day.isoformat())
        return show(s, "PROVINCE:0")

    page = province_page(state)
    if page is not None:
        if value == NEXT and page < len(PAGES) - 1:
            return show(s, f"PROVINCE:{page + 1}")
        if not value.isdigit() or not 1 <= int(value) <= len(PAGES[page]):
            return show(s, state, t["invalid_option"])
        province = PAGES[page][int(value) - 1]
        if s.data.get("mode") == "forecast":
            return reply(build_forecast(s, province), end_session=True)
        return reply(build_result(s, province, date.fromisoformat(s.data["date"])), end_session=True)

    s.stack = []
    return show(s, "MAIN")
