# Loto Signal Redesign + Intelligence V2

Fecha: 2026-05-26

## Resumen Ejecutivo

Scrapperlanas fue rebautizado publicamente como Loto Signal, Buyer Intelligence Console by Loto Software. La app conserva el paquete interno `scrapperlanas` como alias tecnico para no romper imports, tests, despliegues ni datos existentes.

El cambio principal convierte el dashboard en un Inbox inteligente claro y accionable: muestra a quien contactar hoy, por que importa, con que mensaje y cual es el siguiente paso. Tambien se agrego una capa deterministica de Intelligence V2 con score explicable, maquina de estados, motor de siguiente mejor accion, risk engine, evidence engine, dedupe/account intelligence y outreach composer V2.

## Archivos Principales Modificados

- `scrapperlanas/templates/base.html`
- `scrapperlanas/templates/dashboard.html`
- `scrapperlanas/templates/auth/login.html`
- `scrapperlanas/templates/auth/register.html`
- `scrapperlanas/templates/settings.html`
- `scrapperlanas/static/style.css`
- `scrapperlanas/views.py`
- `scrapperlanas/services/intelligence/*`
- `tests/test_intelligence_v2.py`
- `tests/test_app.py`
- `README.md`
- `automation/n8n/README.md`
- `automation/n8n/entrypoint.sh`
- `docker-compose.yml`
- `.env.example`
- `.gitattributes`

Se retiraron `prototipo.html` y `Scrapperlanas-Mockups.html` porque eran mockups viejos con marca anterior, UI no vigente y texto roto por encoding.

## Logo

El archivo `logo.jpg` no existia al iniciar esta entrega. Existia `logosLotos.png` como asset local no versionado, asi que se genero `logo.jpg` desde el cuadrante superior izquierdo de ese mosaico y se copio a `scrapperlanas/static/logo.jpg`.

Decision confirmada por producto: ese `logo.jpg` queda como logo oficial definitivo para Loto Signal.

Uso integrado:

- favicon en `base.html`
- sidebar/header principal
- login y registro
- settings/marca
- fallback textual `Loto Signal` si la imagen no carga

`logosLotos.png` se conserva fuera de git como fuente local auxiliar; la app versiona y usa `logo.jpg`.

## Cambios de Marca

- `Scrapperlanas` visible fue reemplazado por `Loto Signal`.
- `Operating Shell` fue reemplazado por `Buyer Intelligence Console`.
- `Dashboard Inbox` fue reemplazado por `Inbox inteligente`.
- `Cuentas` paso a `Cuentas cliente` en navegacion.
- `Targets` paso a `Targets comerciales`.
- `Visualizacion` fue eliminado de la navegacion principal.
- CSV ahora descarga como `loto-signal-opportunities.csv`.
- n8n muestra `Loto Signal Scheduler` y `Loto Signal Import Webhook`.

Alias conservados:

- paquete Python `scrapperlanas`
- algunos IDs internos de n8n (`scrapperlanas-scheduler`, `scrapperlanas-import-webhook`)
- env var `SCRAPPERLANAS_BASE_URL` como alias de compatibilidad

## Cambios UI/UX

- Nueva shell clara con sidebar, topbar y contenido respirable.
- Paleta premium clara basada en `#F6F8FB`, `#FFFFFF`, `#101828`, `#667085`, `#2563EB`, `#06B6D4`.
- Inbox inteligente como pantalla central.
- Busqueda global con placeholder: `Buscar comprador, skill, pais, fuente o dolor...`
- Filtro de fuente, rango de fechas y riesgo.
- KPI cards maximo 5:
  - Oportunidades activas
  - A1/A2 detectadas
  - Cuentas calientes
  - Valor estimado
  - Follow-ups hoy
- Filtros rapidos:
  - Alta intencion
  - Presupuesto visible
  - Sin contactar
  - Deadline cercano
  - A1/A2
  - Riesgo bajo
  - Con evidencia fuerte
  - Duplicados posibles
- Stage tabs:
  - Todos
  - Nuevo
  - Interesante
  - Aplicado
  - Follow-up
  - Ganado
  - Perdido
  - Sospechoso
  - Descartado
- Bulk actions:
  - Mover etapa
  - Exportar CSV
  - Crear follow-up
  - Marcar descartado
  - Revisar duplicados
- Panel derecho `Copiloto comercial` con resumen, siguiente mejor accion, outreach preview, riesgos y cuentas similares.
- Empty states con explicacion y accion clara.
- Responsive con sidebar colapsable/drawer visual en tablet y movil.

## Intelligence V2

Nuevos modulos en `scrapperlanas/services/intelligence/`:

- `schemas.py`: contratos serializables.
- `utils.py`: normalizacion de texto, fechas, presupuestos, dominios y listas.
- `evidence.py`: evidencia trazable.
- `risk.py`: riesgo y advertencias.
- `scoring.py`: score V2 explicable.
- `next_best_action.py`: accion recomendada.
- `state_machine.py`: estados canonicos y guards.
- `deduplication.py`: similitud de cuentas.
- `outreach.py`: borradores por tono.

## Maquina de Estados

Estados canonicos:

`new`, `triaged`, `interesting`, `outreach_drafted`, `contacted`, `replied`, `proposal_drafted`, `proposal_sent`, `follow_up_due`, `won`, `lost`, `suspicious`, `discarded`, `archived`.

Mapeo legado visible:

- `NUEVO` -> `new`
- `INTERESANTE` -> `interesting`
- `APLICADO` -> `contacted`
- `FOLLOW_UP` -> `follow_up_due`
- `GANADO` -> `won`
- `PERDIDO` -> `lost`
- `SOSPECHOSO` -> `suspicious`
- `DESCARTADO` -> `discarded`

Guards implementados:

- no pasar a `won` sin evidencia, valor ganado o confirmacion manual
- no pasar a `proposal_sent` sin propuesta y canal
- no salir de `suspicious` sin revision manual u override con razon
- no restaurar `discarded` sin restore manual u override con razon
- no generar follow-up sin contacto previo
- no contactar estados bloqueados
- override manual exige razon explicita

## Reglas de Score

Output:

- `grade`: A1/A2/B/C/D
- `numeric_score`: 0-100
- `components`: money, fit, urgency, contactability, confidence, risk_penalty
- `reasons`
- `warnings`
- `suggested_next_action`

Reglas:

- A1: `score >= 90` y riesgo aceptable.
- A2: `score >= 80`.
- B: `score >= 65`.
- C: `score >= 45`.
- D: menor a 45 o riesgo/estado bloqueante.

## Endpoints

Agregados o expuestos:

- `GET /api/intelligence/summary`
- `GET /api/opportunities/<id>/intelligence`
- `POST /api/opportunities/<id>/transition`
- `POST /api/opportunities/<id>/outreach/draft`
- `GET /api/accounts/<id>/merge-suggestions`

Todos requieren login. Los endpoints internos de ingesta siguen protegidos por `CRON_SECRET`.

## Seguridad

Se preservo:

- bcrypt
- CSRF
- headers de seguridad
- rate limiting de auth
- HSTS configurable
- `CRON_SECRET`
- `DATABASE_URL` requerido en produccion
- no impresion de secretos
- no modificacion de `.env` real

Mejoras relevantes:

- los endpoints V2 requieren sesion autenticada
- los drafts se generan de forma deterministica y no prometen experiencia falsa
- la UI usa escaping Jinja por defecto para textos de fuentes externas
- la maquina bloquea transiciones comerciales riesgosas

## Pruebas Ejecutadas

Resultado actual:

```text
python -m pytest -q
79 passed
python -m compileall -q scrapperlanas tests
ok
docker compose config
ok
pip-audit -r requirements.txt
No known vulnerabilities found
pip-audit -r requirements-dev.txt
No known vulnerabilities found
python -m pytest tests/test_ui_playwright.py -q -s
1 passed
Playwright
playwright==1.60.0, Chromium instalado
Servidor smoke
http://127.0.0.1:5051/auth/login 200, /static/logo.jpg 200
```

Pruebas nuevas:

- score V2 explicable
- transicion valida
- transiciones invalidas
- guard de propuesta/canal
- override manual con razon
- next best action
- dedupe confidence
- outreach sin inventar claims
- risk engine con senales de scam

Nota: `docker compose config` carga `.env` local para renderizar. La validacion paso, pero no se documentan ni se imprimen secretos. `pip-audit` se ejecuto desde un venv temporal y fue eliminado al terminar.

Screenshots generados por Playwright:

- `.tmp/playwright/loto-signal-dashboard-empty-desktop.png`
- `.tmp/playwright/loto-signal-dashboard-data-desktop.png`
- `.tmp/playwright/loto-signal-login-mobile.png`

## Como Correr Local

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

Abrir:

```text
http://127.0.0.1:5000
```

## Riesgos Pendientes

- La UI principal fue redisenada profundamente; otras pantallas se armonizaron con CSS y cambios puntuales, pero pueden recibir una segunda pasada visual.
- La deteccion de duplicados V2 ya existe como modulo y endpoint, pero persistir `duplicate_confidence` por cuenta/oportunidad puede ser una mejora posterior.
- No se agregaron migraciones porque la entrega mantuvo cambios compatibles con el esquema actual.
