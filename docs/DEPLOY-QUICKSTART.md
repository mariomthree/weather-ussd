# Deploy rápido (Docker + Nginx)

Guia enxuto. Detalhes, variantes e diagnóstico: [DEPLOY.md](./DEPLOY.md).

## Variáveis

Preencher uma vez e substituir em todos os passos abaixo.

| Placeholder      | O que é                                                      |
|-------------------|----------------------------------------------------------------|
| `<TAG>`            | Tag/versão a fazer deploy                                      |
| `<SERVER_IP>`      | IP onde a app responde (certificado, Nginx, URL do Gateway)     |
| `<SERVER_PORT>`    | Porta pública do Nginx (aberta no firewall)                     |
| `<SERVER_USER>`    | Utilizador SSH para copiar o `.tar` e entrar no servidor        |

A porta interna do container (9001 no loopback → 8080 no container) é fixa e não muda.

## 1. Build (máquina com internet)

```bash
git clone git@github.com:mariomthree/weather-ussd.git
cd weather-ussd
git checkout tags/<TAG>

docker build -t weather-ussd:<TAG> .

docker save -o weather-ussd-<TAG>.tar weather-ussd:<TAG>

scp weather-ussd-<TAG>.tar docker-compose.yml <SERVER_USER>@<SERVER_IP>:/home/<SERVER_USER>
```

Tag da imagem = `<TAG>`, não `latest` — assim `docker images` no servidor
sempre mostra qual versão está carregada, mesmo depois de um `load`
posterior.

## 2. Servidor — carregar imagem

```bash
ssh <SERVER_USER>@<SERVER_IP>

sudo mkdir -p /opt/weather-ussd
sudo chown -R <SERVER_USER>:www-data /opt/weather-ussd
sudo chmod 750 /opt/weather-ussd

mv ~/weather-ussd-<TAG>.tar ~/docker-compose.yml /opt/weather-ussd
cd /opt/weather-ussd

docker load -i weather-ussd-<TAG>.tar

# confirmar qual versão foi carregada
docker images | grep weather-ussd

# pastas dos volumes (base de dados e logs) — a app corre como uid 1000
mkdir -p data logs
sudo chown 1000:1000 data logs
sudo chmod 750 data logs
```

O `docker-compose.yml` referencia `image: weather-ussd:${TAG:-latest}`. O
`docker compose` lê automaticamente o `TAG` do mesmo `.env` da raiz
(`/opt/weather-ussd/.env`) usado no passo 3.

## 3. `.env`

```bash
nano /opt/weather-ussd/.env
```

```env
TAG=<TAG>

PORT=8080
USSD_PATH=/ussd

API_WEATHER_URL=https://api.weatherapi.com/v1/
API_WEATHER_KEY=<chave da WeatherAPI>

DB_PATH=data/weather-ussd.db
RESUME_TTL_MIN=30
WEATHER_HISTORY_DAYS=7
WEATHER_FORECAST_DAYS=2

LOG_DIR=logs
LOG_RETENTION_DAYS=90
```

**Sem aspas nos valores.** Não alterar `PORT`, `DB_PATH` nem `LOG_DIR`.

Tem a chave da WeatherAPI em texto simples — trancar a leitura a só o dono:

```bash
chmod 600 /opt/weather-ussd/.env
```

## 4. Subir container

```bash
docker compose up -d
docker compose ps                     # deve ficar "(healthy)"
docker compose logs -f weather-ussd
```

Em atualizações seguintes (`docker load` da nova imagem e mudar o `TAG` no
`.env`):

```bash
docker compose up -d --force-recreate weather-ussd
docker compose ps   # coluna IMAGE já mostra weather-ussd:<TAG>
```

`data/` e `logs/` mantêm-se entre versões. Rollback: repor o `TAG` anterior e
repetir o comando.

## 5. SSL

Self-signed (sem domínio público — confirmar com a InoveIT que o Gateway o
aceita):

```bash
sudo openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout /etc/ssl/private/weather-ussd.key \
  -out /etc/ssl/certs/weather-ussd.crt \
  -subj "/CN=<SERVER_IP>" -addext "subjectAltName=IP:<SERVER_IP>"

sudo chmod 600 /etc/ssl/private/weather-ussd.key
```

Com domínio público, usar Let's Encrypt:

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d seu-dominio.com
```

## 6. Nginx

`/etc/nginx/sites-available/weather-ussd`:

```nginx
server {
    listen <SERVER_IP>:<SERVER_PORT> ssl;
    server_name <SERVER_IP>;

    ssl_certificate     /etc/ssl/certs/weather-ussd.crt;
    ssl_certificate_key /etc/ssl/private/weather-ussd.key;

    client_max_body_size 16k;

    location / {
        proxy_pass http://127.0.0.1:9001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass_request_headers on;

        proxy_connect_timeout 20s;
        proxy_read_timeout 20s;
        proxy_send_timeout 20s;
    }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/weather-ussd /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Abrir `<SERVER_PORT>` no firewall — de preferência só para o IP do Gateway
(ver [DEPLOY.md](./DEPLOY.md#6-firewall)).

## 7. Testar

Direto no container, sem Nginx (isola problemas de proxy vs aplicação):

```bash
curl http://127.0.0.1:9001/health

curl -sS http://127.0.0.1:9001/ussd \
  -H 'Content-Type: application/json' \
  -d '{"shortcode":"*562*09#","msg":"*562*09#","msisdn":"258840000000","session_id":"deploy-test-1","is_new_session":true}'
```

Via Nginx, com SSL:

```bash
curl -k -sS https://<SERVER_IP>:<SERVER_PORT>/ussd \
  -H 'Content-Type: application/json' \
  -d '{"shortcode":"*562*09#","msg":"*562*09#","msisdn":"258840000000","session_id":"deploy-test-2","is_new_session":true}'
```

(`-k` só é necessário por o certificado ser self-signed.)

URL a registar no Gateway: `https://<SERVER_IP>:<SERVER_PORT>/ussd`. Logs em
`/opt/weather-ussd/logs/ussd.log`.
