"""Configuracao lida de variaveis de ambiente, com suporte a um ficheiro .env."""
import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(_ROOT / ".env")

def _get(key: str, default: str) -> str:
    # Remove aspas tambem aqui: o `docker run --env-file` passa os valores tal como estao no ficheiro.
    return os.environ.get(key, default).strip().strip("'\"")


PORT = int(os.environ.get("PORT", "8080"))
USSD_PATH = _get("USSD_PATH", "/ussd")
API_WEATHER_URL = _get("API_WEATHER_URL", "https://api.weatherapi.com/v1/").rstrip("/") + "/"
API_WEATHER_KEY = _get("API_WEATHER_KEY", "")
# Caminhos relativos sao resolvidos a partir da raiz do projecto (e nao da pasta de onde se corre).
DB_PATH = str(_ROOT / _get("DB_PATH", "data/weather-ussd.db"))
# Tempo durante o qual uma interaccao interrompida pode ser retomada ao voltar a marcar o shortcode.
RESUME_TTL_MIN = int(os.environ.get("RESUME_TTL_MIN", "30"))
# Limites do plano da WeatherAPI (plano gratuito: historico de 7 dias, previsao ate 2 dias a frente).
# Ao mudar de plano basta ajustar estes valores no .env.
WEATHER_HISTORY_DAYS = int(os.environ.get("WEATHER_HISTORY_DAYS", "7"))
WEATHER_FORECAST_DAYS = int(os.environ.get("WEATHER_FORECAST_DAYS", "2"))
# Logs completos (pedidos, respostas, chamadas a WeatherAPI), um ficheiro por dia.
LOG_DIR = str(_ROOT / _get("LOG_DIR", "logs"))
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "90"))
