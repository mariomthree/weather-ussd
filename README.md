# weather-ussd

App USSD de teste da integracao com o **InoveIT USSD Gateway API v2.1** (tarefa USSD_01).
Mostra o estado do tempo por provincia de Mocambique usando a WeatherAPI, em portugues ou ingles.
Python >= 3.10, apenas biblioteca padrao (sem dependencias a instalar).

## Correr
```bash
cp .env.example .env        # preencher API_WEATHER_KEY
python3 -m app.server       # POST http://0.0.0.0:8080/ussd   |  GET /health
python3 simulate.py         # simulador do Gateway no terminal ("x" = cancelar sessao)
```
URL a registar no Gateway: `http(s)://<host>:<PORT><USSD_PATH>`. Sem autenticacao.
Shortcode de testes: `*562*09#` (Tmcel e Vodacom).

## Docker
```bash
docker compose up -d --build      # construir a imagem e arrancar (tambem apos alterar o codigo)
docker compose logs -f            # ver logs em tempo real
docker compose ps                 # estado (deve indicar "healthy")
docker compose down               # parar e remover o contentor
python3 simulate.py http://localhost:9001/ussd   # a porta do contentor so fica no loopback (9001)
```
Volumes (`docker-compose.yml`):
```
./data  ->  /weather-ussd/data   base de dados (data/weather-ussd.db)
./logs  ->  /weather-ussd/logs   logs (logs/ussd.log)
```
A chave da WeatherAPI chega pelo `.env` (`env_file`) e nao fica dentro da imagem.

## Deploy
Deploy no servidor (imagem por `.tar`, Docker Compose, Nginx com SSL, firewall):
[docs/DEPLOY-QUICKSTART.md](docs/DEPLOY-QUICKSTART.md) (resumo) e [docs/DEPLOY.md](docs/DEPLOY.md) (completo).

## Logs
`logs/ussd.log`, um ficheiro por dia (guardados `LOG_RETENTION_DAYS`, 90 por defeito). Regista:
- `[req]`  cada pedido do Gateway: IP de origem, headers e corpo JSON em bruto;
- `[ussd]` sessao, MSISDN, input, ecra anterior -> seguinte;
- `[res]`  resposta enviada, estado HTTP e tempo de resposta;
- `[cleanup]` motivo de fim de sessao; `[spec]` campos que nao constam da Tabela 4.1;
- `weather` chamadas a WeatherAPI (parametros, estado, tempo), sem a chave.

## Menus
```
Estado do Tempo
1. Tempo hoje         -> lista de provincias -> Max, Min, Condicoes de hoje
2. Previsao 3 dias    -> lista de provincias -> Max e Min de hoje e dos 2 dias seguintes
3. Outra data         -> data DDMMYYYY -> lista de provincias -> Max, Min, Condicoes
4. Idioma/Language    -> 1. Portugues / 2. English
0. Sair
```
A previsao de 3 dias usa uma so chamada `forecast.json?days=3` e mostra um dia por linha,
sem condicoes, para caber nos 160 caracteres:
```
Maputo Provincia
Previsao 3 dias

Hoje   29/09 Max 19C Min 17C
Quarta 30/09 Max 23C Min 17C
Quinta 01/10 Max 24C Min 15C
```

**Idioma:** a escolha fica guardada por MSISDN (tabela `preferences`) e vale para as sessoes
seguintes; por defeito e portugues. Muda tudo: menus, meses, dias da semana, nomes das
provincias (Maputo City/Province) e as condicoes da WeatherAPI (`lang=pt` ou sem `lang` em ingles).
A opcao `4. Idioma/Language` aparece sempre nas duas linguas.
Em todos os submenus: `0` = voltar ao menu anterior, `00` = menu principal.
O ecra do resultado e final: e enviado com `end_session: true` e termina a sessao.

A lista de provincias mostra 6 por pagina, com nomes completos; `7.Proximo` abre a pagina
seguinte, que volta a ser numerada a partir de 1. Nas listas, `0` e `00` funcionam mas nao
sao mostrados (na pagina 2, `0` volta a pagina 1).

**Retoma:** se a sessao for interrompida (cancelamento, timeout, queda de rede) fora do menu
principal, ao marcar de novo nos `RESUME_TTL_MIN` minutos seguintes (30 por defeito) aparece:
```
Deseja retomar a sua sessao anterior?
1.Sim   -> volta ao ecra onde estava
2.Nao   -> menu principal
```
Ao sair (`0.Sair`) ou ao chegar ao resultado, o estado e apagado.

## Estrutura
```
app/server.py     endpoint HTTP (spec 4.1/4.2/5.2)
app/menu.py       maquina de estados dos menus
app/weather.py    cliente WeatherAPI (history/forecast, cache, timeout)
app/store.py      SQLite: estado por MSISDN (retoma), lingua por MSISDN e registo de session_id
app/provinces.py  provincias e coordenadas das capitais
app/config.py     leitura do .env
app/logs.py       logs em consola e ficheiro diario
data/             base de dados SQLite (criada ao arrancar)
logs/             logs (criados ao arrancar)
simulate.py       simulador do Gateway
```

## Notas de implementacao
- `cleanup` regista o motivo, mantem o estado para retoma e responde 200 vazio.
- Estado guardado em SQLite (`data/weather-ussd.db`), por isso sobrevive a reinicios do servidor.
- Cada `session_id` fica registado na tabela `sessions` (inicio, fim, motivo) para reconciliacao (spec 3.2).
- Mensagens convertidas para ASCII e limitadas a 160 caracteres.
- Timeout de 8s na WeatherAPI e cache de 10 min por provincia, dia e lingua (deadline do GW ~15s).
- Plano gratuito da WeatherAPI: historico de 7 dias e previsao ate 2 dias a frente. O ecra da data
  mostra o intervalo ("Disponivel: 21/09 a 30/09") e recusa datas fora dele. Ao mudar de plano,
  ajustar `WEATHER_HISTORY_DAYS` e `WEATHER_FORECAST_DAYS` no `.env`.
