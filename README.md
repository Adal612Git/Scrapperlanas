# Scrapperlanas

Scrapperlanas es una consola operativa para detectar, clasificar y dar seguimiento a oportunidades freelance desde una sola bandeja. El repo ya cubre autenticacion, dashboard, trazabilidad, exportacion e ingesta multifuente con endurecimiento web basico para despliegue real.

## Lo que ya hace

- Registro, login y cierre de sesion con `bcrypt`.
- Dashboard con filtros por estado, fuente, sector, riesgo y presupuesto, mas cockpit comercial de targets A1/A2, valor foco y seguimiento.
- Scoring comercial explicable `A1/A2/B/C/D` con desglose de dinero, fit, urgencia, contactabilidad y confianza.
- Normalizacion comercial por oportunidad: tipo de fuente, comprador, dominio, pais, deadline, valor estimado, habilidades, dolor, contacto, evidencia y siguiente mejor accion.
- Pipeline persistente con estados como `NUEVO`, `INTERESANTE`, `APLICADO` y `SOSPECHOSO`.
- Bitacora de eventos por oportunidad.
- Politicas de fuente editables desde la UI.
- Ingesta desde feed demo, Reddit JSON, Workana public projects, GitHub Issues, SAM.gov, Greenhouse, Lever, We Work Remotely RSS, Hacker News Jobs y paginas publicas compatibles.
- Conectores ligeros Fase 5A apagados por defecto: Hacker News Algolia Signals y watchlist ATS para Greenhouse, Lever, Ashby y Workable.
- Procurement Lite Fase 5B apagado por defecto: TED EU, UK Find a Tender, UK Contracts Finder y World Bank Procurement Notices con APIs/JSON estructurados.
- Defaults freelance-first: Workana Public Projects y Reddit activos; Alertas por Correo lista para conectarse a marketplaces como Upwork o Workana via n8n; GitHub Issues y SAM.gov quedan listos como fuentes de mayor intencion, pero apagados hasta configurarlos.
- Resumen y respuesta sugerida con `heuristic`, `Ollama`, `Gemini` o `DeepSeek`.
- Embeddings semanticos opcionales con Ollama o Gemini para mejorar fit y prioridad.
- Exportacion CSV y vistas guardadas.
- Scheduler seguro por fuente via endpoints internos para cron o n8n.
- Importacion interna de oportunidades desde n8n para correo, webhooks o pipelines externos.
- Bootstrap automatico de workflows n8n para scheduler e import webhook.
- Hardening base: CSRF, cookies de sesion, headers de seguridad y arranque sin `debug=True` fijo.
- Soporte de base de datos para SQLite local y PostgreSQL via `DATABASE_URL`.
- Endpoint interno de cron listo para Vercel: `GET /internal/cron/ingest`.

## Stack

- Python 3.12
- Flask
- SQLite para desarrollo local
- PostgreSQL para despliegue
- Gemini API, DeepSeek API u Ollama para enriquecimiento
- requests + BeautifulSoup para conectores HTTP
- Waitress como servidor WSGI de produccion simple

## Arranque local

1. Instala dependencias:

```powershell
python -m pip install -r requirements.txt
```

Para correr pruebas locales:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

2. Crea tu archivo de entorno:

```powershell
Copy-Item .env.example .env
```

3. Ejecuta la app:

```powershell
python run.py
```

4. Abre `http://127.0.0.1:5000`.

## Variables importantes

- `APP_ENV=development|production`
- `SECRET_KEY` obligatorio y fuerte para produccion
- `CRON_SECRET` para proteger la ingesta automatica
- `AUTOMATION_MODE=manual|local|n8n|vercel_cron`
- `AUTOMATION_POLL_SECONDS` para el worker local
- `AUTOMATION_RUN_ON_STARTUP=true|false`
- `DATABASE_PATH` para SQLite local
- `DATABASE_URL` para PostgreSQL
- `AI_PROVIDER=heuristic|ollama|gemini|deepseek`
- `GEMINI_API_KEY` o `DEEPSEEK_API_KEY` segun el proveedor
- `GITHUB_TOKEN` opcional para subir limites del conector GitHub Lead Signals.
- `SAM_API_KEY` obligatorio si habilitas SAM.gov Contract Opportunities.
- `SESSION_COOKIE_SECURE=true` cuando el trafico vaya por HTTPS
- `TRUST_PROXY=true` si esta detras de un proxy reverso
- `AUTH_RATE_LIMIT_MAX_ATTEMPTS` y `AUTH_RATE_LIMIT_WINDOW_SECONDS` para frenar fuerza bruta en login/registro
- `HSTS_ENABLED=true` en produccion HTTPS
- `ALLOW_SQLITE_IN_PRODUCTION=false` y `ALLOW_INSECURE_PRODUCTION_COOKIES=false` deben mantenerse asi salvo pruebas locales controladas
- `AI_EMBED_PROVIDER=gemini|ollama|none` si quieres embeddings semanticos
- `HN_ALGOLIA_ENABLED=false`, `GREENHOUSE_ENABLED=false`, `LEVER_ENABLED=false`, `ASHBY_ENABLED=false`, `WORKABLE_ENABLED=false`
- `WORKABLE_API_TOKEN` si habilitas Workable Target Signals.
- `TED_EU_ENABLED=false`, `UK_FIND_TENDER_ENABLED=false`, `UK_CONTRACTS_FINDER_ENABLED=false`, `WORLD_BANK_PROCUREMENT_ENABLED=false`
- `CONNECTOR_TIMEOUT_SECONDS` y `CONNECTOR_MAX_RESULTS_PER_SOURCE` para limitar conectores ligeros.

## Scoring comercial

Cada oportunidad queda evaluada con:

- `score_money`: presupuesto visible, procurement, compra probable o senal de hiring.
- `score_fit`: match con software, automatizacion, scraping, APIs, data, dashboards y preferencias del perfil.
- `score_urgency`: deadline, frescura y terminos como urgent/asap.
- `score_contactability`: email, telefono, dominio, portal o URL accionable.
- `score_confidence`: evidencia textual, senales de dolor y riesgo.

Los tiers operativos son:

- `A1`: contactar hoy.
- `A2`: revisar esta semana y preparar outreach.
- `B`: nutrir o guardar.
- `C`: ruido de baja prioridad.
- `D`: descartar o ignorar.

## Manual vs automatico

- La IA no corre sola todo el tiempo: corre dentro de cada ingesta o importacion.
- El boton `Ejecutar ingesta ahora` es un override manual para forzar una corrida en ese momento.
- El modo automatico correcto es usar cron o n8n para pegar a los endpoints internos.
- Si `AUTOMATION_MODE=local`, la misma app corre un worker en segundo plano mientras el proceso y la computadora sigan encendidos.
- El scheduler automatico ya respeta `frequency_minutes` por fuente para no castigar boards ni hacer polling innecesario.
- La deduplicacion sigue ocurriendo por `url`, asi que una fuente repetida actualiza en vez de llenar el dashboard con clones.
- Si quieres mejor tasa de freelance real, prioriza `Workana Public Projects` y mete alertas oficiales de Upwork/Workana por correo hacia `email_alerts` en vez de depender solo de ATS corporativos.
- Si quieres senales menos populares, habilita `github_issues` para detectar issues con pago, bounty, contrato o presupuesto; usa `sam_gov` para contratos formales de software/datos cuando tengas `SAM_API_KEY`.
- Si quieres senales comerciales ligeras, usa `/targets` para cargar empresas objetivo y habilita solo la politica de fuente ATS que corresponda: `greenhouse_jobs`, `lever_postings`, `ashby_jobs` o `workable_jobs`. Cada scan agrupa vacantes tecnicas por empresa/dia para evitar spam de oportunidades.
- `hn_algolia` busca conversaciones recientes con frases como `need help with`, `looking for contractor`, `automation`, `dashboard` y `data pipeline`. Normalmente debe tratarse como A2/B salvo que haya compra explicita.
- Procurement Lite evita scraping agresivo y PDFs como fuente principal:
  - `ted_eu`: usa TED Search API `POST /v3/notices/search`.
  - `uk_find_tender`: usa OCDS release packages de Find a Tender.
  - `uk_contracts_finder`: usa OCDS Search de Contracts Finder y excluye awards por default.
  - `worldbank_procurement`: usa `search.worldbank.org/api/procnotices`.
- Estas fuentes filtran por CPV/terminos de software, automatizacion, dashboards, datos, IA, integraciones, web apps, cloud, reporting, ciberseguridad y herramientas internas.
- La app deja log rotativo en `instance/logs/scrapperlanas.log` y heartbeat en `instance/automation-heartbeat.json`.

Endpoints internos importantes:

- `GET /internal/health`
- `GET /internal/automation/policies`
- `GET /internal/automation/policies/due`
- `POST /internal/cron/ingest/policy/<source_key>`
- `GET /internal/cron/ingest`
- `POST /internal/import/opportunities`

## Proveedores de IA

### Gemini recomendado

```powershell
$env:AI_PROVIDER="gemini"
$env:GEMINI_API_KEY="tu-api-key"
$env:GEMINI_MODEL="gemini-2.5-flash-lite"
$env:AI_EMBED_PROVIDER="gemini"
$env:GEMINI_EMBED_MODEL="gemini-embedding-001"
python run.py
```

### DeepSeek listo para cambio rapido

```powershell
$env:AI_PROVIDER="deepseek"
$env:DEEPSEEK_API_KEY="tu-api-key"
$env:DEEPSEEK_MODEL="deepseek-chat"
$env:AI_EMBED_PROVIDER="gemini"
python run.py
```

## Ollama opcional

```powershell
$env:AI_PROVIDER="ollama"
$env:OLLAMA_ENABLED="true"
$env:OLLAMA_URL="http://localhost:11434"
$env:OLLAMA_MODEL="mistral:latest"
$env:OLLAMA_EMBED_MODEL="nomic-embed-text:latest"
python run.py
```

Si el modelo configurado no existe, Scrapperlanas intenta elegir automaticamente el mejor disponible segun `OLLAMA_MODEL_PREFERENCES`.

## Produccion en Vercel

1. Conecta una base PostgreSQL real a tu proyecto de Vercel y define `DATABASE_URL`.
2. Define `SECRET_KEY` y `CRON_SECRET` fuertes, de al menos 32 caracteres.
3. Si usaras Gemini, define `AI_PROVIDER=gemini`, `GEMINI_API_KEY` y opcionalmente `AI_EMBED_PROVIDER=gemini`.
4. El archivo `vercel.json` ya agenda una corrida diaria de ingesta sobre `/internal/cron/ingest`.

Sin `DATABASE_URL`, la app rechaza arrancar en produccion por defecto. Puedes usar `ALLOW_SQLITE_IN_PRODUCTION=true` solo para pruebas efimeras controladas.

Ese endpoint ahora procesa solo las fuentes vencidas, no todo el inventario completo en cada disparo.

En produccion, el bootstrap deshabilita `sample_feed` y limpia cualquier demo data anterior para dejar el tablero solo con oportunidades reales.

## Docker + PostgreSQL

1. Crea `.env` a partir de `.env.example`.
2. Cambia `SECRET_KEY`, `CRON_SECRET`, `POSTGRES_PASSWORD`, `N8N_BASIC_AUTH_PASSWORD` y `N8N_ENCRYPTION_KEY` por valores reales.
3. Levanta el stack:

```powershell
docker compose up --build
```

La app queda en `http://127.0.0.1:5000`, n8n en `http://127.0.0.1:5678` y la base en el contenedor `db`.

Define tambien en `.env`:

- `AUTOMATION_MODE=n8n`
- `N8N_BASIC_AUTH_USER`
- `N8N_BASIC_AUTH_PASSWORD`
- `N8N_ENCRYPTION_KEY`
- `POSTGRES_PASSWORD`
- `SCRAPPERLANAS_BASE_URL=http://web:8000`
- `N8N_PUBLIC_URL=http://localhost:5678`
- `N8N_IMPORT_WEBHOOK_PATH=scrapperlanas/import`

El compose ya no trae passwords operativos por default: si falta alguno de esos secretos, Docker falla antes de levantar servicios inseguros. `N8N_IMAGE` esta fijado en `.env.example` para despliegues reproducibles y se puede actualizar de forma explicita.

Al levantar `docker compose up --build`, el contenedor de `n8n` importa automaticamente dos workflows empaquetados:

- `Scrapperlanas Scheduler`: dispara `GET /internal/cron/ingest` cada `N8N_SCHEDULER_INTERVAL_MINUTES`.
- `Scrapperlanas Import Webhook`: expone `POST /webhook/<N8N_IMPORT_WEBHOOK_PATH>` y reenvia el JSON al import interno de Scrapperlanas.

La guia operativa para n8n vive en [automation/n8n/README.md](C:/Users/Rick/Documents/LotosTechnologies/Scrapperlanas/automation/n8n/README.md).

## Modo local sin Docker

Si tu compu esta prendida y no quieres depender de n8n, usa:

- `AUTOMATION_MODE=local`
- `AUTOMATION_POLL_SECONDS=300`

Luego arranca la app normalmente:

```powershell
python run.py
```

En ese modo, Scrapperlanas:

- revisa fuentes vencidas en segundo plano
- ejecuta IA dentro de cada ingesta
- registra corridas en `automation_runs`
- expone salud en `GET /internal/health`
- deja logs en `instance/logs/scrapperlanas.log`

## Pruebas

```powershell
python -m pytest
```

## Pendientes de producto

- Cobertura de pruebas mas profunda sobre conectores remotos y flujos de configuracion.
