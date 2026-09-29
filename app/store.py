"""Persistencia em SQLite.

- states:   estado da navegacao por MSISDN. Sobrevive ao fim da sessao USSD, para o
            assinante poder retomar onde parou (cancelamento, timeout, queda de rede).
            E apagado quando a interaccao termina normalmente.
- sessions: registo de cada session_id, chave de reconciliacao com a InoveIT (spec 3.2).
- preferences: lingua escolhida por MSISDN (pt/en). Permanente, ao contrario de states.
"""
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from . import config

_lock = threading.Lock()
os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
_db = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
_db.execute("PRAGMA journal_mode=WAL")
_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS states (
        msisdn      TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL,
        stack       TEXT NOT NULL,
        data        TEXT NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
        session_id  TEXT PRIMARY KEY,
        msisdn      TEXT,
        shortcode   TEXT,
        started_at  TEXT NOT NULL,
        ended_at    TEXT,
        end_reason  TEXT
    );
    CREATE TABLE IF NOT EXISTS preferences (
        msisdn      TEXT PRIMARY KEY,
        lang        TEXT NOT NULL,
        updated_at  REAL NOT NULL
    );
    """
)

DEFAULT_LANG = "pt"


@dataclass
class State:
    msisdn: str
    session_id: str
    stack: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    lang: str = DEFAULT_LANG


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_state(msisdn: str, session_id: str) -> State:
    """Estado guardado do MSISDN (se ainda dentro do prazo de retoma) ou um estado novo."""
    with _lock:
        row = _db.execute(
            "SELECT stack, data, updated_at FROM states WHERE msisdn = ?", (msisdn,)
        ).fetchone()
        pref = _db.execute("SELECT lang FROM preferences WHERE msisdn = ?", (msisdn,)).fetchone()
    lang = pref[0] if pref else DEFAULT_LANG
    if row and time.time() - row[2] < config.RESUME_TTL_MIN * 60:
        return State(msisdn, session_id, json.loads(row[0]), json.loads(row[1]), lang)
    return State(msisdn, session_id, lang=lang)


def save_state(st: State) -> None:
    with _lock:
        _db.execute(
            "INSERT INTO states (msisdn, session_id, stack, data, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(msisdn) DO UPDATE SET session_id = excluded.session_id, stack = excluded.stack, "
            "data = excluded.data, updated_at = excluded.updated_at",
            (st.msisdn, st.session_id, json.dumps(st.stack), json.dumps(st.data), time.time()),
        )


def set_lang(msisdn: str, lang: str) -> None:
    with _lock:
        _db.execute(
            "INSERT INTO preferences (msisdn, lang, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(msisdn) DO UPDATE SET lang = excluded.lang, updated_at = excluded.updated_at",
            (msisdn, lang, time.time()),
        )


def clear_state(msisdn: str) -> None:
    with _lock:
        _db.execute("DELETE FROM states WHERE msisdn = ?", (msisdn,))


def session_started(session_id: str, msisdn: str, shortcode: str | None) -> None:
    with _lock:
        _db.execute(
            "INSERT OR IGNORE INTO sessions (session_id, msisdn, shortcode, started_at) VALUES (?, ?, ?, ?)",
            (session_id, msisdn, shortcode, _now_iso()),
        )


def session_ended(session_id: str, reason: str) -> None:
    with _lock:
        _db.execute(
            "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE session_id = ? AND ended_at IS NULL",
            (_now_iso(), reason, session_id),
        )
