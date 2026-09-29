# Deploy completo (Docker + Nginx)

Procedimento de ponta a ponta para pôr a app USSD a correr atrás de um Nginx
com SSL, num IP e porta específicos, para registar o URL no InoveIT USSD
Gateway. Pensado para replicar igual no servidor UAT.

Este guia usa **placeholders** em vez de valores fixos. Antes de começar,
defina os seus e substitua-os de forma consistente em **todos** os passos
(`.env`, certificado, Nginx, firewall e testes) — misturar valores é o erro
mais comum.

| Placeholder | Significa | Exemplo |
| --- | --- | --- |
| `<TAG>` | Tag/versão a fazer deploy | `1.0.0` |
| `<SERVER_IP>` | IP onde a app responde (o mesmo no certificado, Nginx e URL do Gateway) | `10.0.0.10` |
| `<SERVER_PORT>` | Porta pública do Nginx (a que fica aberta no firewall) | `2012` |
| `<SERVER_USER>` | Utilizador SSH usado para copiar o `.tar` e entrar no servidor | `deploy` |

A porta interna do container (**9001** no loopback → **8080** no container) é
uma convenção fixa deste guia e não precisa de ser trocada. É a 9001 (e não a
9000) para não colidir com o `uba-ussd-bridge` se ficarem no mesmo servidor.

## 1. Build da imagem e transferência para o servidor

A imagem é construída numa máquina e, como o servidor de destino não tem acesso
a um registry, empacota-se num `.tar`, copia-se por `scp` e carrega-se no
servidor. Feito isto, o resto do guia corre já no servidor.

### 1.1. Na máquina de build — construir e gerar o `.tar`

```bash
git clone git@github.com:mariomthree/weather-ussd.git
cd weather-ussd
git checkout tags/<TAG>

docker build -t weather-ussd:<TAG> .

docker save -o weather-ussd-<TAG>.tar weather-ussd:<TAG>

# confirmar que ficou (deve ter ~150 MB, não 0 bytes)
ls -lh weather-ussd-<TAG>.tar
```

Tag da imagem = `<TAG>`, não `latest` — assim `docker images` no servidor
sempre mostra qual versão está carregada, mesmo depois de um `load` posterior.

### 1.2. Transferir com `scp` (porta 22)

```bash
scp weather-ussd-<TAG>.tar docker-compose.yml <SERVER_USER>@<SERVER_IP>:/home/<SERVER_USER>
```

Se o SSH do servidor não estiver na porta 22, indica-a com `-P <porta>`
(no `scp` é `-P` maiúsculo; no `ssh` é `-p` minúsculo).

### 1.3. No servidor — preparar a pasta e carregar a imagem

A partir daqui, **todos os passos correm no servidor**.

```bash
ssh <SERVER_USER>@<SERVER_IP>

# criar a pasta dedicada do projecto — /opt é o local correcto para software
# instalado fora do gestor de pacotes da distro, isolado de /var/www (Nginx)
# e /home (utilizadores)
sudo mkdir -p /opt/weather-ussd
sudo chown -R <SERVER_USER>:www-data /opt/weather-ussd
sudo chmod 750 /opt/weather-ussd

# mover para a pasta
mv ~/weather-ussd-<TAG>.tar ~/docker-compose.yml /opt/weather-ussd
cd /opt/weather-ussd

docker load -i weather-ussd-<TAG>.tar

# confirmar que a imagem foi importada
docker images | grep weather-ussd
```

`chmod 750` fecha a pasta a "outros" (só `<SERVER_USER>` e o grupo `www-data`
lêem/entram) — o `.env` lá dentro tem a chave da WeatherAPI (secção 2), por
isso a pasta não pode ficar `755`. Como o `chown` passou a pasta para o
`<SERVER_USER>`, o `mv` já não precisa de `sudo`.

### 1.4. Pastas dos volumes (`data/` e `logs/`)

A app guarda a base de dados SQLite em `data/` e os logs diários em `logs/`,
montados do host (`./data` e `./logs` no `docker-compose.yml`). Dentro do
container a app corre como o utilizador **uid 1000** (sem privilégios), por
isso as pastas do host têm de pertencer a esse uid:

```bash
cd /opt/weather-ussd
mkdir -p data logs
sudo chown 1000:1000 data logs
sudo chmod 750 data logs
```

Se não forem criadas antes, o Docker cria-as como `root` e a app falha ao
arrancar com `PermissionError` (não consegue criar `data/weather-ussd.db` nem
`logs/ussd.log`). O `<SERVER_USER>` pode não ter uid 1000 — confirme com
`id -u` — por isso o `chown` usa o uid numérico e não o nome.

Estas pastas **sobrevivem às atualizações**: recriar o container não apaga a
base de dados (estado de retoma e registo de sessões) nem os logs.

### 1.5. Atualizações

O `docker-compose.yml` referencia `image: weather-ussd:${TAG:-latest}` — o
`docker compose` lê automaticamente um `.env` na raiz
(`/opt/weather-ussd/.env`, ao lado do `docker-compose.yml`), o mesmo ficheiro
usado para as variáveis da app (secção 2). Basta incluir lá:

```env
TAG=<TAG>
```

Numa atualização (o `.env` já existe, só muda o valor de `TAG`), depois de
`docker load` da nova imagem e de editar o `.env`:

```bash
docker compose up -d --force-recreate weather-ussd
docker compose ps   # coluna IMAGE confirma a versão de facto a correr
```

**Rollback:** a imagem anterior continua carregada (`docker images`). Basta
repor o `TAG` antigo no `.env` e repetir o `up -d --force-recreate`.

## 2. Preparar o `.env`

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

`TAG` é lido pelo `docker compose` para escolher a imagem
(`weather-ussd:${TAG:-latest}`, ver secção 1.5); as restantes chegam ao
container pelo `env_file`.

**Não altere `PORT`, `DB_PATH` nem `LOG_DIR`.** O mapeamento de portas
(`9001:8080`), o `HEALTHCHECK` da imagem e os volumes (`data/`, `logs/`)
assumem estes valores. Para mudar a porta pública, mude o Nginx (passo 5).

**Sem aspas nos valores.** O `env_file` do `docker-compose.yml` não faz
parsing de shell: `PORT='8080'` chega ao container com as aspas incluídas e a
app não arranca (`ValueError: invalid literal for int()`).

Tem a chave da WeatherAPI em texto simples — trancar a leitura a só o dono:

```bash
chmod 600 /opt/weather-ussd/.env
```

`600` (`rw-------`) deixa só o dono (`<SERVER_USER>`) ler/escrever. O
`docker compose` corre como `<SERVER_USER>`, por isso continua a conseguir ler
o `env_file` normalmente.

O `.env` nunca é commitado nem entra na imagem (`.dockerignore`) — fica só no
servidor.

## 3. Subir o container com Docker Compose

O `docker-compose.yml` já vem definido no repositório (e foi copiado no passo
1.2):

```yaml
services:
  weather-ussd:
    build: .
    image: weather-ussd:${TAG:-latest}
    container_name: weather-ussd
    restart: unless-stopped
    env_file: .env
    ports:
      - "127.0.0.1:9001:8080"
    volumes:
      - ./data:/weather-ussd/data
      - ./logs:/weather-ussd/logs
    networks:
      - weather_ussd_network

networks:
  weather_ussd_network:
    name: weather_ussd_network
```

```bash
cd /opt/weather-ussd
docker compose up -d
```

Não uses `--build` — no servidor não há código fonte; a imagem já foi
carregada via `docker load` no passo 1.3 e o compose só a referencia por nome.
(O `build: .` existe apenas para o desenvolvimento local.)

```bash
docker compose ps                     # STATUS deve passar a "(healthy)" em ~30s
docker compose logs -f weather-ussd   # "weather-ussd a escutar em http://0.0.0.0:8080/ussd"
```

Notas:

- `127.0.0.1:9001:8080` no `ports:`: publica a porta **9001 do host** (ligada
  só ao loopback) para a porta **8080 do container**. Assim só o Nginx local
  consegue falar com o container; a porta não fica exposta para fora da
  máquina — o mesmo padrão usado pelo `uba-ussd-bridge`.
- `restart: unless-stopped`: volta a subir sozinho se o Docker/servidor
  reiniciar.
- A imagem já traz `TZ=Africa/Maputo` — as datas nos logs e o cálculo de
  "hoje" no menu ficam em hora de Maputo, independentemente do fuso do
  servidor.
- O servidor HTTP é o da biblioteca padrão (`ThreadingHTTPServer`), sem
  Gunicorn — não há `command:` nem workers para configurar.

Para recriar depois de alterar o `.env` (o container não relê o ficheiro em
runtime):

```bash
docker compose up -d --force-recreate weather-ussd
```

## 4. Certificado SSL

Self-signed (ambiente interno/UAT sem domínio público). Um certificado por
IP precisa do `subjectAltName` explícito:

```bash
sudo mkdir -p /etc/ssl/private

sudo openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout /etc/ssl/private/weather-ussd.key \
  -out /etc/ssl/certs/weather-ussd.crt \
  -subj "/CN=<SERVER_IP>" \
  -addext "subjectAltName=IP:<SERVER_IP>"

sudo chmod 600 /etc/ssl/private/weather-ussd.key
```

**Confirmar com a InoveIT** que o Gateway aceita certificado self-signed. Se
não aceitar, as alternativas são um domínio público com Let's Encrypt (abaixo)
ou registar o URL em `http://` (Nginx sem `ssl`) — a app não tem autenticação,
por isso nesse caso restrinja o firewall ao IP do Gateway (secção 6).

Se houver domínio público, usar Let's Encrypt em vez disto:

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d seu-dominio.com
```

## 5. Configurar o Nginx

```bash
sudo nano /etc/nginx/sites-available/weather-ussd
```

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

Escolhas deliberadas:

- **`X-Real-IP` / `X-Forwarded-For`.** Atrás do Nginx a app vê sempre
  `from=127.0.0.1` nos logs `[req]`; o IP real do Gateway fica registado nos
  headers do mesmo log. `X-Forwarded-For $remote_addr` sobrescreve (não anexa)
  para o valor não poder ser forjado pelo cliente.
- **`client_max_body_size 16k`.** O mesmo limite que a app aplica
  (`MAX_BODY`); pedidos maiores são cortados já no Nginx.
- **Timeouts de 20s.** Ver 5.1.

Ativar e recarregar:

```bash
sudo ln -s /etc/nginx/sites-available/weather-ussd /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 5.1. Cadeia de timeouts

A regra é **Nginx > deadline do Gateway > timeout da WeatherAPI** — cada camada
dá folga à de dentro:

| Camada | Valor | Onde se define |
| --- | --- | --- |
| Nginx `proxy_*_timeout` | **20s** | `sites-available/weather-ussd` (passo 5) |
| Deadline do Gateway (InoveIT) | **~15s** | Lado do Gateway, não configurável aqui |
| Timeout da WeatherAPI | **8s** | `app/weather.py` (fixo no código) |

A app responde sempre dentro do deadline do Gateway (no pior caso, 8s à espera
da WeatherAPI + processamento), por isso o Nginx nunca deve ser o primeiro a
desistir. Se a WeatherAPI não responder, a app responde "Servico indisponivel. Tente
mais tarde." em vez de deixar o Gateway em timeout.

## 6. Firewall

O IP e porta a abrir são `<SERVER_IP>` e `<SERVER_PORT>` (a porta pública do
Nginx — a 9001 do container fica no loopback e **não** deve ser aberta).

A app **não tem autenticação** (o Gateway não envia credenciais). Sempre que
possível, abra a porta apenas para o IP de origem do Gateway da InoveIT
(`<GATEWAY_IP>`, a pedir à InoveIT ou a ler nos headers dos logs `[req]`)
em vez de `any`.

### 6.1. Descobrir qual firewall está ativo

Numa mesma máquina normalmente só um destes está a gerir as regras. Corra os
comandos por ordem e veja qual responde com regras reais:

```bash
# ufw (frontend mais comum em Ubuntu/Debian)
sudo ufw status verbose
#   -> "Status: active"  => está a usar ufw  (vá para 6.2)

# firewalld (comum em RHEL/CentOS/Rocky)
sudo firewall-cmd --state
#   -> "running" => está a usar firewalld (vá para 6.3)

# nftables (backend moderno; pode estar a ser usado diretamente)
sudo nft list ruleset | head

# iptables (backend clássico / usado por baixo do ufw e do firewalld)
sudo iptables -S
sudo iptables -t nat -S
```

Nota: `ufw` e `firewalld` são apenas frontends — as regras acabam sempre em
`iptables` ou `nftables`.

### 6.2. Se for `ufw`

```bash
# abrir só para o Gateway (preferível)
sudo ufw allow from <GATEWAY_IP> to <SERVER_IP> port <SERVER_PORT> proto tcp
# ou, se ainda não souber o IP do Gateway:
sudo ufw allow from any to <SERVER_IP> port <SERVER_PORT> proto tcp

# confirmar
sudo ufw status numbered | grep <SERVER_PORT>
```

### 6.3. Se for `firewalld`

```bash
# abrir só para o Gateway (preferível)
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="<GATEWAY_IP>" port port="<SERVER_PORT>" protocol="tcp" accept'
# ou, para qualquer origem:
sudo firewall-cmd --permanent --add-port=<SERVER_PORT>/tcp

sudo firewall-cmd --reload
sudo firewall-cmd --list-all
```

### 6.4. Se for `iptables` puro

```bash
sudo iptables -A INPUT -p tcp -s <GATEWAY_IP> -d <SERVER_IP> --dport <SERVER_PORT> -j ACCEPT

sudo iptables -L INPUT -n -v --line-numbers | grep <SERVER_PORT>
```

Em iptables puro as regras não são persistentes por defeito — para sobreviver
a um reboot use `netfilter-persistent save` (Debian/Ubuntu) ou
`iptables-save > /etc/iptables/rules.v4`.

### 6.5. Confirmar que a porta está mesmo exposta

```bash
# deve aparecer o Nginx a ouvir em <SERVER_IP>:<SERVER_PORT>, não em 127.0.0.1
sudo ss -tlnp | grep <SERVER_PORT>

# teste a partir de OUTRA máquina da rede
nc -vz <SERVER_IP> <SERVER_PORT>
curl -k -sS -o /dev/null -w '%{http_code}\n' https://<SERVER_IP>:<SERVER_PORT>/health
```

Se `ss` mostrar a porta ligada a `127.0.0.1:<SERVER_PORT>` em vez de
`<SERVER_IP>:<SERVER_PORT>`, o problema é o `listen` do Nginx (passo 5), não o
firewall.

### 6.6. Saída para a WeatherAPI

O container tem de conseguir sair para `api.weatherapi.com:443`. Se a máquina
tiver firewall de saída, liberte esse destino (ver "Diagnóstico rápido").

## 7. Testar

Health check direto no container, sem Nginx:

```bash
curl http://127.0.0.1:9001/health
# {"status": "ok"}
```

Pedido USSD direto no container (isola problemas de proxy vs aplicação):

```bash
curl -sS http://127.0.0.1:9001/ussd \
  -H 'Content-Type: application/json' \
  -d '{"shortcode":"*562*09#","msg":"*562*09#","msisdn":"258840000000","session_id":"deploy-test-1","is_new_session":true}'
# {"message": "Temperatura Mocambique\n\n1. Temperatura de hoje\n...", "end_session": false}
```

Via Nginx, com SSL:

```bash
curl -k -sS https://<SERVER_IP>:<SERVER_PORT>/ussd \
  -H 'Content-Type: application/json' \
  -d '{"shortcode":"*562*09#","msg":"*562*09#","msisdn":"258840000000","session_id":"deploy-test-2","is_new_session":true}'
```

(`-k` só é necessário por o certificado ser self-signed.)

Fluxo completo com o simulador (de uma máquina com o repositório):

```bash
python3 simulate.py https://<SERVER_IP>:<SERVER_PORT>/ussd
```

O simulador usa `urllib` e valida o certificado; com self-signed, corra-o no
servidor contra `http://127.0.0.1:9001/ussd`.

Por fim, registar no Gateway da InoveIT o URL
`https://<SERVER_IP>:<SERVER_PORT>/ussd` e marcar `*562*09#` num telemóvel
(Tmcel ou Vodacom). Cada pedido deve aparecer em `/opt/weather-ussd/logs/ussd.log`:

```bash
tail -f /opt/weather-ussd/logs/ussd.log
```

## Diagnóstico rápido

| Sintoma | Causa provável | Verificar |
| --- | --- | --- |
| Container em `Restarting` com `PermissionError` em `data/` ou `logs/` | Pastas dos volumes criadas pelo Docker como `root` | `ls -ln /opt/weather-ussd` — `data`/`logs` devem ser `1000 1000` (passo 1.4) |
| `ValueError: invalid literal for int()` no arranque | Aspas num valor numérico do `.env` (`PORT`, `RESUME_TTL_MIN`, ...) | `docker compose exec weather-ussd env \| grep -E 'PORT\|_DAYS\|_MIN'` |
| Container `unhealthy` | `PORT` no `.env` diferente de 8080 (o healthcheck bate sempre na 8080) | `docker inspect --format '{{json .State.Health}}' weather-ussd` |
| `502 Bad Gateway` | Container em baixo ou em `Restarting` | `docker compose ps` e `docker compose logs --tail 50 weather-ussd` |
| `404 {"error": "not found"}` no POST | URL registado no Gateway não bate com `USSD_PATH` | Comparar o path em `[req]` nos logs com `USSD_PATH` no `.env` |
| Ecrã "Servico indisponivel. Tente mais tarde." e `[call] ... erro=` nos logs | Sem saída para a WeatherAPI, ou `API_WEATHER_KEY` inválida | Teste de conectividade abaixo; `grep '\[call\]' logs/ussd.log` mostra o estado HTTP |
| Nada aparece nos logs ao marcar o shortcode | O Gateway não chega ao servidor (firewall, IP/porta errados, SSL recusado) | `sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log` e secção 6.5 |
| `port is already allocated` no `docker compose up` | Outro container/serviço já usa a porta 9001 | `docker ps --filter publish=9001` |
| `nginx -t` falha | Erro de sintaxe ou certificado inexistente no caminho indicado | Conferir se `/etc/ssl/certs/weather-ussd.crt` e `/etc/ssl/private/weather-ussd.key` existem |

**Testar conectividade à WeatherAPI**, de dentro do container e do host, para
saber se é isolamento do Docker ou da máquina toda:

```bash
# de dentro do container
docker compose exec weather-ussd python -c \
  "import socket; socket.create_connection(('api.weatherapi.com', 443), timeout=10); print('OK 443')"

# do host
curl -sS -o /dev/null -w '%{http_code}\n' --connect-timeout 10 https://api.weatherapi.com/v1/
```

Se funcionar do host mas não do container, é isolamento de rede do Docker —
verificar se o `weather_ussd_network` tem saída. Se falhar dos dois, é rede da
máquina — libertar a saída para `api.weatherapi.com:443` com a equipa de rede.
