# Scrapperlanas Buyer Intelligence V2.1

Fecha: 2026-05-26

## Resumen ejecutivo

Scrapperlanas no es un scraper de ofertas. Es una consola de inteligencia comercial que convierte ruido publico en oportunidades verificables, priorizadas y accionables.

Esta fase agrega memoria de calidad, explicaciones humanas, UI de control, reproceso seguro, golden dataset y bloqueo centralizado de outreach. La prioridad fue reducir falsos positivos y mantener trazabilidad: cuando una oportunidad cambia por feedback humano, se conserva la evaluacion automatica original y se deja huella.

## Problema que resuelve

"Mas leads" no sirve si la bandeja se llena con ficcion, conspiraciones, SEO, discusiones, agregadores, presupuestos falsos o compradores inexistentes. V2.1 hace que el sistema recuerde decisiones humanas, audite fuentes y falle tests si vuelven casos como `nosleep`, `conspiracy`, `rate my proposal`, `30+ jobs`, `3-6 months` o `10-15%`.

## Archivos principales modificados

- `scrapperlanas/services/quality.py`
- `scrapperlanas/services/quality_memory.py`
- `scrapperlanas/services/lead_explainer.py`
- `scrapperlanas/services/pipeline.py`
- `scrapperlanas/services/outreach.py`
- `scrapperlanas/services/intelligence/outreach.py`
- `scrapperlanas/services/buyer_intelligence.py`
- `scrapperlanas/views.py`
- `scrapperlanas/templates/quality.html`
- `scrapperlanas/templates/dashboard.html`
- `scrapperlanas/templates/opportunity_detail.html`
- `tests/fixtures/opportunity_quality_golden.json`

## Quality Memory

Nueva tabla: `quality_feedback`.

Decisiones soportadas:

- `GOOD_LEAD`
- `BAD_LEAD`
- `SCAM`
- `DUPLICATE`
- `NOT_COMMERCIAL`
- `LOW_VALUE`
- `WRONG_BUDGET`
- `WRONG_BUYER`
- `WRONG_SKILLS`
- `NEEDS_REVIEW`

Modulo: `scrapperlanas/services/quality_memory.py`.

Funciones:

- `record_feedback(...)`
- `get_feedback_for_opportunity(...)`
- `get_feedback_stats(...)`
- `apply_feedback_overrides(...)`

Regla: el feedback humano puede cambiar `quality_stage`, `grade`, presupuesto o comprador, pero `analysis_json.quality_auto` conserva la evaluacion automatica original.

## Lead Explainer

Modulo: `scrapperlanas/services/lead_explainer.py`.

Devuelve:

- `headline`
- `verdict`: `CONTACT_NOW`, `RESEARCH_FIRST`, `WATCH`, `DO_NOT_CONTACT`, `REJECTED`
- razones positivas
- razones negativas
- evidencia faltante
- siguiente accion
- confianza

Se integra en:

- Cards del Inbox inteligente.
- Detalle de oportunidad.
- Copiloto V2.
- Export de auditoria CSV.

## Quality Command Center

Ruta: `/quality`.

Incluye:

- Salud de ingesta.
- Calidad por fuente.
- Top razones de rechazo.
- Reglas editables para Reddit.
- Ultimos falsos positivos.
- Acciones rapidas seguras.
- Estado del golden dataset.

Las reglas se guardan en `source_policies.config_json` para Reddit:

- allowlist
- greylist
- blocklist
- minimum commercial intent score
- minimum buyer confidence
- hide rejected by default
- reject critical risk
- hide duplicates

## Copiloto V2

El copiloto ahora tiene tres modos:

- Evaluar: veredicto, razones, bloqueos y evidencia faltante.
- Investigar: checklist sin afirmar que investigó por fuera.
- Redactar: genera mensaje solo si la oportunidad es contactable.

Bloqueos centralizados:

- `quality_stage != READY_TO_CONTACT`
- riesgo `HIGH` o `CRITICAL`
- comprador con confianza menor a 50
- intencion `FICTION`, `CONSPIRACY`, `NEWS`, `DISCUSSION`, `SEO_CONTENT`, `SELF_PROMO`, `MARKET_RESEARCH`
- estados `DESCARTADO` o `SOSPECHOSO` sin override humano

La ruta persistida de drafts ahora usa el mismo guardrail. Si no se puede redactar, guarda un draft de investigacion, no un pitch comercial.

## Golden dataset

Fixture: `tests/fixtures/opportunity_quality_golden.json`.

Contiene mas de 40 casos con expected:

- `expected_quality_stage`
- `expected_commercial_intent_label`
- `expected_contactable`
- `expected_budget_valid`
- `expected_grade_max`

Casos cubiertos:

- Basura Reddit: `nosleep`, `conspiracy`, `WritingPrompts`, `news`.
- SEO: `How to Choose...`.
- Discusion: `review/rate my proposal`.
- Presupuestos falsos: `30+ jobs`, `3-6 months`, `10-15%`.
- Low value: `$15 for 20 minutes`, `$20/$30` tareas chicas.
- Leads buenos sinteticos: n8n, ISP MXN, law firm, clinic, ecommerce, government RFP.
- Ambiguos: founder sin presupuesto, tool recommendations, job aggregator.

## Source trust learning

`get_feedback_stats` calcula feedback reciente por fuente y autor. Dos SCAM en la misma fuente aplican penalizacion alta; varios negativos bajan trust. Ese ajuste se aplica durante ingesta y reproceso por `apply_feedback_overrides(..., stats=...)`.

## Safe reprocessing

Comando:

```powershell
flask --app scrapperlanas recompute-quality --limit 500
```

Tambien existe accion UI en `/quality`.

Resumen esperado:

```txt
Processed: N | Updated: N | Rejected: N | Duplicates hidden: N | Errors: N
```

No reingesta, no duplica oportunidades y conserva feedback humano.

## Hot Accounts V2

Las cuentas calientes ahora ignoran ruido y senales debiles:

- No se calientan subreddits como `conspiracy` o `nosleep`.
- `reddit.com` con comprador debil no puede ser hot account.
- El score de cuenta suma solo senales utilizables.
- Si el ruido supera senales validas, la cuenta pasa a `noisy`.

## Exports

Ruta nueva:

- `/exports/quality-audit.csv`

Columnas principales:

- `qualityStage`
- `commercialIntentLabel`
- `riskLevel`
- `riskReasons`
- `buyerConfidence`
- `budgetRawEvidence`
- `sourceTrustScore`
- `authorTrustScore`
- `domainTrustScore`
- `duplicateClusterId`
- `isContactable`
- `explanationHeadline`
- `nextBestAction`

## Seguridad

Se mantuvieron:

- bcrypt
- CSRF
- headers de seguridad
- rate limiting de auth
- CRON_SECRET
- validacion de produccion con DATABASE_URL

No se agregan secretos ni se imprimen tokens. El nuevo feedback usa formularios POST protegidos por CSRF y endpoints autenticados.

## Tests agregados

- `tests/test_quality_golden_dataset.py`
- `tests/test_quality_memory.py`
- `tests/test_lead_explainer.py`
- Nuevas pruebas en:
  - `tests/test_quality_firewall.py`
  - `tests/test_intelligence_v2.py`
  - `tests/test_outreach.py`
  - `tests/test_buyer_intelligence.py`
  - `tests/test_app.py`

## Validacion ejecutada

Resultados finales:

- `python -m compileall -q scrapperlanas tests`: passed.
- `python -m pytest tests/test_quality_golden_dataset.py tests/test_quality_memory.py tests/test_lead_explainer.py -q`: 48 passed.
- `python -m pytest tests/test_quality_firewall.py tests/test_intelligence_v2.py tests/test_outreach.py tests/test_buyer_intelligence.py tests/test_app.py -q`: 74 passed.
- `python -m pytest -q`: 150 passed.
- `python -m playwright install chromium`: passed.
- `python -m pytest tests/test_ui_playwright.py -q -s`: 1 passed.
- `ruff check .`: passed.
- `pip-audit -r requirements.txt -r requirements-dev.txt`: no known vulnerabilities found.

## Riesgos pendientes

- Las reglas editables guardan config y reprocesan datos existentes, pero no implementan un editor avanzado de versionado de reglas.
- El checklist de investigacion no hace busquedas externas; solo indica que falta validar.
- El trust learning es deterministico por conteo reciente; una fase posterior puede agregar ventanas temporales por fuente/autor.
- El golden dataset usa fixtures sinteticos; conviene ampliarlo con muestras anonimizadas reales cuando existan.

## Como correr local

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python run.py
```

Abrir:

- `http://127.0.0.1:5000`
- `/quality` para Quality Command Center.
