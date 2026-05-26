# n8n para Loto Signal

Loto Signal hace el trabajo pesado en Python: conectores, normalizacion, scoring, Intelligence V2, deduplicacion, persistencia y eventos. n8n solo orquesta corridas y recibe oportunidades externas.

Por compatibilidad, algunas variables y rutas internas conservan el alias legado `scrapperlanas`.

## Workflows Empaquetados

Al levantar `docker compose up --build`, n8n importa y activa:

- `Loto Signal Scheduler`
- `Loto Signal Import Webhook`

Los IDs internos siguen siendo `scrapperlanas-scheduler` y `scrapperlanas-import-webhook` para no romper instalaciones existentes.

## URLs Internas

- Desde Docker: `http://web:8000`
- Desde host local: `http://127.0.0.1:5000`

Todas las rutas internas requieren:

- Header `Authorization: Bearer <CRON_SECRET>`

No pongas el `CRON_SECRET` en workflows visibles al usuario final ni en repositorios.

## Endpoints Listos

- `GET /internal/automation/policies`
  Devuelve fuentes habilitadas con `last_run_at`, `next_run_at`, `is_due` y `frequency_minutes`.

- `GET /internal/automation/policies/due`
  Devuelve solo fuentes vencidas.

- `POST /internal/cron/ingest/policy/<source_key>`
  Ejecuta una sola fuente y respeta frecuencia por defecto.

- `GET /internal/cron/ingest`
  Ejecuta todas las fuentes vencidas. Sirve para cron simple o compatibilidad.

- `POST /internal/import/opportunities`
  Inserta o actualiza oportunidades normalizadas desde n8n.

## Scheduler Recomendado

El workflow empaquetado corre cada `N8N_SCHEDULER_INTERVAL_MINUTES` y llama:

```text
GET {{$env.SCRAPPERLANAS_BASE_URL}}/internal/cron/ingest
```

`SCRAPPERLANAS_BASE_URL` se mantiene como alias de compatibilidad. Puedes documentarlo internamente como URL base de Loto Signal.

Con esto:

- cada fuente corre solo cuando vence su `frequency_minutes`
- el Inbox inteligente no depende del boton manual
- se evita polling agresivo contra fuentes publicas

## Import Webhook

El workflow `Loto Signal Import Webhook` expone:

```text
POST {{$env.N8N_PUBLIC_URL}}/webhook/{{$env.N8N_IMPORT_WEBHOOK_PATH}}
```

El default de `N8N_IMPORT_WEBHOOK_PATH` ahora es `loto-signal/import`. Si ya tienes webhooks antiguos, puedes dejar el path legado configurado manualmente.

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

- Si la `url` ya existe, Loto Signal actualiza en vez de duplicar.
- Intelligence V2 corre dentro de la importacion igual que en las ingestas normales.
- `email_alerts` soporta `min_score` y `max_age_days` en su politica.
- Para marketplaces freelance, usa alertas oficiales por correo de Upwork/Workana hacia `email_alerts`.
- Para reimportar workflows empaquetados, levanta n8n con `N8N_BOOTSTRAP_FORCE=true`.

## Reglas Practicas

- Empieza con scheduler cada 15 minutos.
- Manten `frequency_minutes` por fuente en 60-180 para boards publicos.
- Usa `include_terms`, `exclude_terms`, `remote_only`, `require_budget`, `max_items`, `min_score` y `max_age_days`.
- El boton del Inbox inteligente es override manual, no el mecanismo principal de automatizacion.
