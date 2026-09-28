"""Simulador do Gateway no terminal: envia os mesmos payloads que o USSD GW (spec 4.1/5.2).

Uso: python simulate.py [URL] [SHORTCODE] [MSISDN]
Escreva a resposta a cada ecra; "x" cancela a sessao (envia cleanup USER_CANCELLED).
"""
import json
import secrets
import sys
import urllib.error
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/ussd"
SHORTCODE = sys.argv[2] if len(sys.argv) > 2 else "*562*09#"
MSISDN = sys.argv[3] if len(sys.argv) > 3 else "258841234567"
SESSION_ID = secrets.token_hex(5).upper()


def post(payload: dict) -> tuple[int, dict | None]:
    body = json.dumps({"shortcode": SHORTCODE, "msisdn": MSISDN, "session_id": SESSION_ID, **payload})
    req = urllib.request.Request(URL, body.encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    return status, json.loads(raw) if raw else None


def main():
    print(f"Sessao {SESSION_ID} -> {URL} (marca {SHORTCODE})\n")
    status, r = post({"msg": SHORTCODE, "is_new_session": True, "event_type": "ussd_request"})
    while True:
        if status != 200:
            print(f"[HTTP {status}] {r}")
            return
        print("+----------------------------------+")
        print(r["message"])
        print(f"+--------------------------- {len(r['message']):>3}c -+")
        if r["end_session"]:
            return
        value = input("> ")
        if value.strip().lower() == "x":
            status, _ = post({"msg": "USER_CANCELLED", "is_new_session": False, "event_type": "cleanup"})
            print(f"[cleanup] HTTP {status}")
            return
        status, r = post({"msg": value, "is_new_session": False, "event_type": "ussd_request"})


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
    except urllib.error.URLError as err:
        sys.exit(f"Erro: {err.reason}")
