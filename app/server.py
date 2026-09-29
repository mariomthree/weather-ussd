"""Endpoint do Content Provider para o InoveIT USSD Gateway API v2.1.

O Gateway faz POST JSON (spec 4.1) sem autenticacao; respondemos com
{message, end_session} (spec 4.2) ou 200 vazio para eventos de cleanup (spec 5.2).

Uso: python -m app.server
"""
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config, logs, store
from .menu import handle

MAX_BODY = 16 * 1024

log = logging.getLogger("weather-ussd")


class UssdHandler(BaseHTTPRequestHandler):
    server_version = "weather-ussd/1.0"

    def log_message(self, fmt, *args):  # o registo e feito em _send, com mais detalhe
        pass

    def _begin(self) -> None:
        """Regista o pedido tal como chegou: origem, linha HTTP, headers e corpo em bruto."""
        self._t0 = time.monotonic()
        # O healthcheck do Docker corre a cada 30s; nao entra nos logs para nao os encher.
        self._quiet = urlparse(self.path).path == "/health"
        self._raw = b""
        length = int(self.headers.get("Content-Length") or 0)
        self._too_large = length > MAX_BODY
        if 0 < length <= MAX_BODY:
            self._raw = self.rfile.read(length)
        if self._quiet:
            return
        log.info(
            "[req] from=%s %s %s headers=%s body=%s",
            self.client_address[0],
            self.command,
            self.path,
            json.dumps(dict(self.headers.items())),
            self._raw.decode("utf-8", "replace") if length <= MAX_BODY else f"<{length} bytes, ignorado>",
        )

    def _send(self, status: int, body: dict | None = None) -> None:
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        if self._quiet:
            return
        log.info(
            "[res] status=%d time=%dms body=%s",
            status,
            (time.monotonic() - self._t0) * 1000,
            data.decode("utf-8") or "<vazio>",
        )

    def do_GET(self):
        self._begin()
        if urlparse(self.path).path == "/health":
            return self._send(200, {"status": "ok"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        self._begin()
        if urlparse(self.path).path != config.USSD_PATH:
            return self._send(404, {"error": "not found"})
        try:
            self._on_ussd()
        except Exception:
            log.exception("[500] erro ao processar o pedido")
            self._send(500, {"error": "internal error"})

    def _on_ussd(self):
        if self._too_large:
            return self._send(413, {"error": "payload demasiado grande"})
        try:
            p = json.loads(self._raw or b"")
            if not isinstance(p, dict):
                raise ValueError
        except ValueError:
            log.warning("[400] JSON invalido")
            return self._send(400, {"error": "JSON invalido"})

        sid = p.get("session_id")
        msisdn = p.get("msisdn")
        msg = p.get("msg")
        if not sid or not msisdn:
            log.warning("[400] session_id/msisdn em falta")
            return self._send(400, {"error": "session_id/msisdn em falta"})

        # Campos fora da Tabela 4.1 ajudam a detectar diferencas entre o Gateway e a documentacao.
        extra = set(p) - {"shortcode", "msg", "msisdn", "session_id", "is_new_session", "event_type"}
        if extra:
            log.warning("[spec] campos nao documentados: %s", sorted(extra))

        if p.get("event_type") == "cleanup":
            # O estado do MSISDN fica guardado para poder retomar na proxima sessao.
            store.session_ended(sid, msg or "CLEANUP")
            log.info("[cleanup] session=%s msisdn=%s reason=%s", sid, msisdn, msg)
            return self._send(200)

        is_new = p.get("is_new_session") is True
        if is_new:
            store.session_started(sid, msisdn, p.get("shortcode"))
        st = store.load_state(msisdn, sid)
        before = list(st.stack)
        lang_before = st.lang

        out = handle(st, msg, is_new)
        if st.lang != lang_before:
            store.set_lang(msisdn, st.lang)
        if out["end_session"]:
            store.clear_state(msisdn)
            store.session_ended(sid, "COMPLETED")
        else:
            store.save_state(st)
        log.info(
            "[ussd] session=%s msisdn=%s new=%s lang=%s msg=%r ecra=%s->%s end=%s len=%d",
            sid, msisdn, is_new, st.lang, msg, before[-1:] or "-", "FIM" if out["end_session"] else st.stack[-1:],
            out["end_session"], len(out["message"]),
        )
        self._send(200, out)


def main():
    logs.setup()
    server = ThreadingHTTPServer(("0.0.0.0", config.PORT), UssdHandler)
    log.info(
        "weather-ussd a escutar em http://0.0.0.0:%d%s (logs em %s)",
        config.PORT, config.USSD_PATH, config.LOG_DIR,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
