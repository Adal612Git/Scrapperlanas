# Loto Signal

Buyer Intelligence Console by Loto Software.

Loto Signal es una consola operativa para detectar, priorizar y dar seguimiento a oportunidades comerciales/freelance desde una sola bandeja. El alias interno del paquete Python sigue siendo `scrapperlanas` para conservar compatibilidad con imports, rutas historicas, bases SQLite existentes y despliegues anteriores.

## Capacidades

- Login, registro y cierre de sesion con `bcrypt`.
- UI premium clara: Inbox inteligente, KPI cards, filtros rapidos, pipeline, cuentas cliente, targets comerciales, settings y exportacion CSV.
- Ingesta multifuente: demo, Reddit, Workana, GitHub Issues, SAM.gov, Greenhouse, Lever, Ashby, Workable, Hacker News Algolia, TED EU, UK Find a Tender, UK Contracts Finder y World Bank Procurement.
- Normalizacion de comprador, dominio, pais, presupuesto, deadline, skills, dolores, contacto, evidencia y URL de accion.
- Intelligence V2 deterministica: score explicable A1/A2/B/C/D, risk engine, evidence engine, dedupe/account intelligence, next best action y outreach composer.
- Pipeline comercial con estados historicos y maquina canonica: `new`, `triaged`, `interesting`, `outreach_drafted`, `contacted`, `replied`, `proposal_drafted`, `proposal_sent`, `follow_up_due`, `won`, `lost`, `suspicious`, `discarded`, `archived`.
- Copiloto comercial con resumen de pipeline, deadlines, fuente de mejor calidad, riesgos, borrador de outreach y accion recomendada.
- Agrupacion de compradores en cuentas, deteccion de duplicados y merge.
- Vistas guardadas, metricas, tablero, detalle de oportunidades y exportacion CSV.
- Automatizacion por cron local, Vercel cron o n8n.
- Seguridad base: CSRF, headers de seguridad, cookies endurecidas, rate limiting de auth, `CRON_SECRET`, HSTS configurable y `DATABASE_URL` obligatorio en produccion salvo override explicito.

## Stack

- Python 3.12
- Flask + Jinja
- SQLite local y PostgreSQL via `DATABASE_URL`
- Gemini, DeepSeek u Ollama para enriquecimiento opcional
- requests + BeautifulSoup para conectores HTTP
- Waitress para WSGI simple de produccion
- pytest para pruebas

## Arranque Local

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

Luego abre `http://127.0.0.1:5000`.

Para pruebas:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## Variables Importantes

- `APP_ENV=development|production`
- `SECRET_KEY` obligatorio y fuerte para produccion.
- `CRON_SECRET` protege endpoints internos de ingesta.
- `DATABASE_PATH` conserva SQLite local.
- `DATABASE_URL` es requerido en produccion por defecto.
- `SESSION_COOKIE_SECURE=true` cuando el trafico vaya por HTTPS.
- `TRUST_PROXY=true` si corre detras de proxy reverso.
- `AUTH_RATE_LIMIT_MAX_ATTEMPTS` y `AUTH_RATE_LIMIT_WINDOW_SECONDS` limitan fuerza bruta.
- `HSTS_ENABLED=true` para produccion HTTPS.
- `ALLOW_SQLITE_IN_PRODUCTION=false` y `ALLOW_INSECURE_PRODUCTION_COOKIES=false` deben mantenerse cerrados salvo pruebas controladas.
- `AI_PROVIDER=heuristic|ollama|gemini|deepseek`.
- `AI_EMBED_PROVIDER=gemini|ollama|none`.
- `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `GITHUB_TOKEN`, `SAM_API_KEY` segun proveedor/fuente.
- `AUTOMATION_MODE=manual|local|n8n|vercel_cron`.
- `N8N_IMPORT_WEBHOOK_PATH` usa por defecto `loto-signal/import`; el path legado puede seguir configurandose manualmente para webhooks existentes.
- `SCRAPPERLANAS_BASE_URL` se conserva como alias de compatibilidad para n8n.

No guardes secretos reales en el repo. Usa `.env` local o variables del proveedor de despliegue.

## Intelligence V2

Los modulos viven en `scrapperlanas/services/intelligence/`:

- `scoring.py`: score V2 explicable con componentes de dinero, fit, urgencia, contactabilidad, confianza y penalizacion de riesgo.
- `state_machine.py`: transiciones canonicas con guards y override manual con razon.
- `next_best_action.py`: motor de accion recomendada.
- `outreach.py`: borradores por tono sin inventar credenciales falsas.
- `deduplication.py`: similitud por dominio, comprador, pais, contactos, skills y dolores.
- `risk.py`: senales de scam, deadline vencido, baja identidad, duplicado y evidencia pobre.
- `evidence.py`: evidencia humana para explicar score y recomendaciones.
- `schemas.py`: contratos dataclass serializables.

Endpoints autenticados nuevos:

- `GET /api/intelligence/summary`
- `GET /api/opportunities/<id>/intelligence`
- `POST /api/opportunities/<id>/transition`
- `POST /api/opportunities/<id>/outreach/draft`
- `GET /api/accounts/<id>/merge-suggestions`

## Automatizacion

Endpoints internos protegidos por `Authorization: Bearer <CRON_SECRET>`:

- `GET /internal/health`
- `GET /internal/automation/policies`
- `GET /internal/automation/policies/due`
- `POST /internal/cron/ingest/policy/<source_key>`
- `GET /internal/cron/ingest`
- `POST /internal/import/opportunities`

La app deja heartbeat en `instance/runtime/automation-heartbeat.json`. El nombre de log puede mantenerse legado en instalaciones existentes por compatibilidad.

## Docker + PostgreSQL

1. Crea `.env` desde `.env.example`.
2. Define valores reales para `SECRET_KEY`, `CRON_SECRET`, `POSTGRES_PASSWORD`, `N8N_BASIC_AUTH_PASSWORD` y `N8N_ENCRYPTION_KEY`.
3. Levanta:

```powershell
docker compose up --build
```

La app queda en `http://127.0.0.1:5000`, n8n en `http://127.0.0.1:5678` y PostgreSQL en el contenedor `db`.

El bootstrap de n8n importa:

- `Loto Signal Scheduler`
- `Loto Signal Import Webhook`

Guia operativa: [automation/n8n/README.md](automation/n8n/README.md).

## Produccion

- Define `DATABASE_URL`, `SECRET_KEY` y `CRON_SECRET`.
- Usa HTTPS con cookies seguras y HSTS.
- Si usas Vercel, `vercel.json` agenda `/internal/cron/ingest`.
- Sin `DATABASE_URL`, Loto Signal rechaza arrancar en produccion por defecto.
- En produccion, el bootstrap deshabilita demo data para dejar solo oportunidades reales.

## Documentacion Operativa

- [Redisenio e inteligencia](docs/operacion/loto-signal-redesign-intelligence-2026-05-26.md)
- [Maquina de estados](docs/operacion/loto-signal-state-machine.md)
- [Intelligence V2](docs/operacion/loto-signal-intelligence-v2.md)

## Pruebas

```powershell
python -m pytest -q
```
