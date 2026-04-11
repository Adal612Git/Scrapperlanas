# n8n para Scrapperlanas

Scrapperlanas ya hace el trabajo pesado en Python:

- scraping y conectores remotos
- scoring base
- enriquecimiento con IA
- deduplicacion por `url`
- persistencia y eventos

n8n debe orquestar, no rehacer esa logica.

Este repo ya trae bootstrap automatico para tres workflows empaquetados:

- `Scrapperlanas Scheduler`
- `Scrapperlanas Import Webhook`
- `Scrapperlanas Email Alerts IMAP`

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

## Workflow minimo para email_alerts por IMAP

El workflow `Scrapperlanas Email Alerts IMAP`:

1. lee mensajes no leidos del buzón IMAP configurado en n8n
2. usa el asunto como `title`
3. intenta extraer la primera `url` del cuerpo
4. concatena asunto + cuerpo como `raw_text`
5. manda el payload a `POST /internal/import/opportunities`

Credencial que debes crear en n8n:

- Tipo: `IMAP`
- Nombre sugerido: `Scrapperlanas IMAP`
- Host: el host IMAP de tu proveedor
- Puerto: `993` si usas SSL/TLS
- Usuario: tu buzón dedicado
- Password: tu password o app password
- SSL/TLS: `true` en la mayoría de proveedores modernos

Si usas Gmail:

- Host: `imap.gmail.com`
- Puerto: `993`
- SSL/TLS: `true`
- Credencial: app password, no la contraseña normal si tu cuenta tiene 2FA

Payload final que el workflow manda a Scrapperlanas:

```json
{
  "source_key": "email_alerts",
  "source_label": "Alertas por Correo",
  "items": [
    {
      "external_id": "message-id-o-uid",
      "title": "Asunto del correo",
      "company": "Remitente",
      "url": "https://enlace-detectado-si-existe",
      "raw_text": "asunto + cuerpo plano",
      "posted_at": "2026-04-09T00:00:00.000Z",
      "budget_min": null,
      "budget_max": null,
      "currency": "USD",
      "stack": [],
      "risk_level": "low",
      "risk_reasons": []
    }
  ]
}
```

## Reglas practicas

- No hagas polling agresivo. Empieza con scheduler cada 15 minutos.
- Manten `frequency_minutes` por fuente en 60-180 para boards publicos.
- Usa `include_terms`, `exclude_terms`, `remote_only`, `require_budget`, `max_items`, `min_score` y `max_age_days`.
- El boton del dashboard es solo override manual.
