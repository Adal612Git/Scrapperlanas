# Scrapperlanas V3 / Loto Signal Demo Readiness

Fecha: 2026-05-26

## Resumen ejecutivo

Esta fase convierte la consola en una superficie mas vendible y operable: el usuario puede ver si el producto esta listo para demo, auditar la calidad de fuentes y restaurar cambios de reglas sin tocar codigo. La promesa de producto queda mas clara: Loto Signal no busca mostrar muchas ofertas; busca separar ruido publico de oportunidades comerciales verificables.

Pitch interno:

> Loto Signal no es un scraper de ofertas. Es una consola de inteligencia comercial que convierte ruido publico en oportunidades verificables, priorizadas y accionables.

## Cambios principales

- Se renombro la vista de calidad a `Centro de Control de Calidad`.
- Se agrego un panel `Demo readiness` con score, checklist y pruebas operativas visibles.
- Se agrego historial de reglas de calidad con version, hash, resumen, motivo y estado activo.
- Se agrego rollback desde UI para restaurar una version anterior de reglas.
- Se agrego snapshot inicial automatico de reglas de Reddit para que cualquier cambio quede comparado contra una base auditable.
- Se agrego screenshot Playwright de la vista de calidad.

## Quality Rules Versioning

Nueva tabla:

```txt
quality_rule_versions
```

Campos relevantes:

- `source_key`
- `version_number`
- `config_hash`
- `config_json`
- `reason`
- `change_summary`
- `is_active`
- `actor_user_id`
- `created_at`

Reglas:

- La primera apertura del Centro de Control crea un snapshot inicial si no existe.
- Cada guardado de reglas crea una version nueva activa.
- Solo una version por fuente queda activa.
- Restaurar una version antigua no borra historia: crea una version nueva activa con la configuracion restaurada.

## UI / UX

La vista de calidad ahora sirve para una demo comercial:

- Muestra si hay datos suficientes para ensenar el producto.
- Explica si existe dataset golden y cuantas pruebas anti-basura cubre.
- Muestra contactables reales, rechazados por ruido y duplicados controlados.
- Permite ajustar allowlist, greylist, blocklist y umbrales de calidad.
- Pide resumen y motivo para cada cambio.
- Muestra historial de reglas y boton `Restaurar`.

## Demo narrative sugerida

1. Abrir Inbox inteligente y mostrar que la lista principal no esta dominada por ruido.
2. Entrar al Centro de Control de Calidad.
3. Mostrar `Demo readiness` como prueba de operacion.
4. Abrir calidad por fuente y explicar tasa contactable vs tasa basura.
5. Mostrar razones de rechazo: subreddit bloqueado, SEO, presupuesto invalido, sin comprador.
6. Modificar una regla menor con motivo escrito.
7. Mostrar que se crea una version nueva.
8. Restaurar la version anterior para demostrar rollback seguro.
9. Exportar auditoria CSV.

## Archivos modificados

- `scrapperlanas/db.py`
- `scrapperlanas/services/quality_rules.py`
- `scrapperlanas/views.py`
- `scrapperlanas/templates/quality.html`
- `scrapperlanas/static/style.css`
- `tests/test_app.py`
- `tests/test_quality_rules.py`
- `tests/test_ui_playwright.py`

## Como correr local

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium
python run.py
```

URL local usada en desarrollo:

```txt
http://127.0.0.1:5053
```

## Validacion

Comandos de validacion de esta fase:

```bash
python -m compileall -q scrapperlanas tests
python -m pytest -q
ruff check .
python -m playwright install chromium
python -m pytest tests/test_ui_playwright.py -q -s
pip-audit -r requirements.txt -r requirements-dev.txt
```

Resultados locales:

```txt
python -m compileall -q scrapperlanas tests        passed
python -m pytest -q                               153 passed
ruff check .                                      passed
python -m playwright install chromium             passed
python -m pytest tests/test_ui_playwright.py -q -s 1 passed
pip-audit -r requirements.txt -r requirements-dev.txt no known vulnerabilities
```

Screenshots generados por Playwright en `.tmp/playwright/`:

- `loto-signal-dashboard-empty-desktop.png`
- `loto-signal-dashboard-data-desktop.png`
- `loto-signal-quality-command-center.png`
- `loto-signal-login-mobile.png`

## Riesgos pendientes

- El snapshot inicial se crea al abrir el Centro de Control si aun no existe historial; es intencional para no meter una migracion de backfill agresiva.
- El rollback restaura configuracion, pero no reprocesa automaticamente oportunidades existentes. El operador debe usar `Recalcular calidad`.
- El readiness score mide preparacion operativa basica; no reemplaza una suite E2E completa ni pruebas con datos reales de produccion.
