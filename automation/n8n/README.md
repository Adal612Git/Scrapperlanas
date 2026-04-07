# n8n para Scrapperlanas

Scrapperlanas ya hace el trabajo pesado en Python:

- scraping y conectores remotos
- scoring base
- enriquecimiento con IA
- deduplicacion por `url`
- persistencia y eventos

n8n debe orquestar, no rehacer esa logica.

Este repo ya trae bootstrap automatico para dos workflows empaquetados:

- `Scrapperlanas Scheduler`
- `Scrapperlanas Import Webhook`

Cuando levantas `docker compose up --build`, `n8n` importa esos workflows al arranque y los deja activos.

## URLs internas

- Desde Docker: `http://web:8000`
- Desde host local: `http://127.0.0.1:5000`

Todas las rutas internas usan:

- Header `Authorization: Bearer <CRON_SECRET>`

## Endpoints listos

- `GET /internal/automation/policies`
  Devuelve fuentes habilitadas con `last_run_at`, `next_run_at`, `is_due` y `frequency_minutes`.

- `GET /internal/automation/policies/due`
  Devuelve solo las fuentes vencidas. Este es el endpoint recomendado para el scheduler.

- `POST /internal/cron/ingest/policy/<source_key>`
  Ejecuta una sola fuente. Respeta frecuencia por defecto.

- `GET /internal/cron/ingest`
  Ejecuta todas las fuentes vencidas. Sirve para cron simple o compatibilidad.

- `POST /internal/import/opportunities`
  Inserta o actualiza oportunidades normalizadas desde n8n. Ideal para correo, formularios o webhooks.

## Workflow recomendado: scheduler real sin castigar fuentes

El workflow empaquetado usa una ruta mas robusta para bootstrap inicial:

1. `Schedule Trigger`
   Corre cada `N8N_SCHEDULER_INTERVAL_MINUTES`.

2. `HTTP Request`
   `GET {{$env.SCRAPPERLANAS_BASE_URL}}/internal/cron/ingest`

Con esto:

- cada fuente corre solo cuando vence su `frequency_minutes`
- el dashboard no depende de apretar un boton
- el import inicial es mas simple de validar y desplegar

## Workflow recomendado: correo a oportunidades

El workflow empaquetado `Scrapperlanas Import Webhook` deja este endpoint en `n8n`:

- `POST {{$env.N8N_PUBLIC_URL}}/webhook/{{$env.N8N_IMPORT_WEBHOOK_PATH}}`

Ese webhook:

1. acepta un item, un array o un objeto con `items`
2. normaliza la carga minima
3. reenvia el payload a `POST {{$env.SCRAPPERLANAS_BASE_URL}}/internal/import/opportunities`

Payload minimo:

```json
{
  "source_key": "email_alerts",
  "source_label": "Alertas por Correo",
  "items": [
    {
      "external_id": "gmail-123",
      "title": "Automation engineer contract",
      "company": "Acme",
      "url": "https://example.com/jobs/automation-engineer",
      "raw_text": "Remote contract. Need Python, n8n and APIs. Budget USD 2500.",
      "posted_at": "2026-04-01T18:00:00Z",
      "budget_min": 2500,
      "currency": "USD",
      "stack": ["Python", "n8n", "APIs"],
      "risk_level": "low",
      "risk_reasons": []
    }
  ]
}
```

Notas:

- Si la `url` ya existe, Scrapperlanas actualiza en vez de duplicar.
- La IA corre dentro de la importacion igual que en los scrapers normales.
- `email_alerts` soporta `min_score` y `max_age_days` en su politica para no meter ruido.
- Para marketplaces con mejor probabilidad freelance, usa alertas oficiales por correo de Upwork y Workana y canalizalas aqui; es mas estable que scrapear HTML privado.
- Si quieres reimportar los workflows empaquetados, levanta `n8n` con `N8N_BOOTSTRAP_FORCE=true`.

## Reglas practicas

- No hagas polling agresivo. Empieza con scheduler cada 15 minutos.
- Manten `frequency_minutes` por fuente en 60-180 para boards publicos.
- Usa `include_terms`, `exclude_terms`, `remote_only`, `require_budget`, `max_items`, `min_score` y `max_age_days`.
- El boton del dashboard es solo override manual.
