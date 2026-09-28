FROM python:3.12-slim

# Hora de Maputo nos logs e no calculo de "hoje"
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Africa/Maputo \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /weather-ussd
COPY app ./app
COPY simulate.py README.md ./

# Utilizador sem privilegios (uid 1000 = normalmente o dono das pastas montadas no host)
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p data logs \
    && chown app:app data logs
USER app

# A chave da WeatherAPI e as restantes variaveis chegam por --env-file (nao ficam na imagem)
EXPOSE 8080
VOLUME ["/weather-ussd/data", "/weather-ussd/logs"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["python", "-m", "app.server"]
