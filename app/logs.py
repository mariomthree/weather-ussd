"""Configuracao de logs: consola + ficheiro diario em LOG_DIR (logs/ussd.log, rodado a meia-noite).

Durante os testes de integracao guarda-se tudo (payloads completos do Gateway e respostas),
para servir de evidencia na analise com a InoveIT. A chave da WeatherAPI nunca e registada.
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

from . import config

FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def setup() -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        os.path.join(config.LOG_DIR, "ussd.log"),
        when="midnight",
        backupCount=config.LOG_RETENTION_DAYS,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    handlers = [logging.StreamHandler(), file_handler]
    for h in handlers:
        h.setFormatter(logging.Formatter(FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
