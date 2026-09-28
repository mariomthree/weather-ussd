"""Maquina de estados dos menus USSD.

A navegacao usa uma pilha de ecras: "0" volta ao ecra anterior, "00" ao menu principal.
A pilha e guardada por MSISDN (ver store.py); se a sessao anterior foi interrompida,
ao marcar de novo o assinante pode retomar no ecra onde estava.
"""
import logging
import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .provinces import PROVINCES
from .store import State
from .weather import WeatherUnavailable, available_range, get_day_weather

log = logging.getLogger(__name__)

MAX_LEN = 160  # spec 4.2: maximo 160 caracteres ASCII
NAV = "0. Voltar\n00. Menu Principal"
TZ = ZoneInfo("Africa/Maputo")


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


MONTHS = ["Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
          "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def full_date(d: date) -> str:
    """28-Setembro-2026"""
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def fmt(n: float) -> str:
    return f"{round(n)}C"


# Cada ecra: titulo, linha em branco, opcoes.
def screen_main(s: State, notice: str) -> str:
    return "Temperatura Mocambique\n\n1. Temperatura de hoje\n2. Temperatura de um dia especifico\n0. Sair"


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


def screen_province(s: State, notice: str, page: int) -> str:
    # "0" (voltar) e "00" (menu) funcionam aqui, mas nao sao mostrados para simplificar a lista.
    d = date.fromisoformat(s.data["date"])
    title = notice or ("Temperatura de hoje" if d == today() else f"Temperatura de {full_date(d)}")
    lines = [f"{title}\n\nEscolha a provincia:", *(f"{n}. {p['name']}" for n, p in enumerate(PAGES[page], 1))]
    if page < len(PAGES) - 1:
        lines.append(f"\n{NEXT}. Proximo")
    return "\n".join(lines)


def screen_ask_date(s: State, notice: str) -> str:
    first, last = available_range(today())
    return (f"Temperatura de um dia especifico\n\nIntroduza a data (DDMMYYYY)\n"
            f"Disponivel: {first:%d/%m} a {last:%d/%m}\n\n{NAV}")


def screen_resume(s: State, notice: str) -> str:
    return "Deseja retomar a sua sessao anterior?\n\n1. Sim\n2. Nao"


SCREENS = {
    "RESUME": screen_resume,
    "MAIN": screen_main,
    "ASK_DATE": screen_ask_date,
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


def build_result(province: dict, day: date) -> str:
    """Ecra final: e mostrado com end_session=true, por isso nao leva opcoes de navegacao."""
    header = f"{province['name']}\n{full_date(day)}\n\n"
    try:
        w = get_day_weather(province["q"], day, today())
    except WeatherUnavailable:
        return f"{header}Sem dados para esta data."
    except Exception as err:  # rede, timeout, resposta inesperada
        log.error("[weather] %s %s: %s", province["name"], day, err)
        return f"{header}Servico indisponivel. Tente mais tarde."

    return f"{header}Max: {fmt(w['max'])}\nMin: {fmt(w['min'])}\nCondicoes: {w['condition']}"


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

    if state == "RESUME":
        if value == "1":
            s.stack = s.data.pop("resume_stack")
            return reply(render(s, s.stack[-1]))
        if value in ("2", "00"):
            s.stack, s.data = [], {}
            return show(s, "MAIN")
        return show(s, "RESUME", "Opcao invalida.")

    if state != "MAIN":
        if value == "00":
            s.stack = []
            return show(s, "MAIN")
        if value == "0":
            return go_back(s)

    if state == "MAIN":
        if value == "1":
            s.data["date"] = today().isoformat()
            return show(s, "PROVINCE:0")
        if value == "2":
            return show(s, "ASK_DATE")
        if value == "0":
            return reply("Obrigado por usar o servico.", end_session=True)
        return show(s, "MAIN", "Opcao invalida.")

    if state == "ASK_DATE":
        day = parse_date(value)
        if day is None:
            return show(s, "ASK_DATE", "Data invalida.")
        first, last = available_range(today())
        if not first <= day <= last:
            return show(s, "ASK_DATE", "Data fora do intervalo.")
        s.data["date"] = day.isoformat()
        return show(s, "PROVINCE:0")

    page = province_page(state)
    if page is not None:
        if value == NEXT and page < len(PAGES) - 1:
            return show(s, f"PROVINCE:{page + 1}")
        if not value.isdigit() or not 1 <= int(value) <= len(PAGES[page]):
            return show(s, state, "Opcao invalida.")
        province = PAGES[page][int(value) - 1]
        return reply(build_result(province, date.fromisoformat(s.data["date"])), end_session=True)

    s.stack = []
    return show(s, "MAIN")
