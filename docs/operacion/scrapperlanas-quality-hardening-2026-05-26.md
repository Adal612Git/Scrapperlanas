# Scrapperlanas / Loto Signal Quality Hardening - 2026-05-26

## Resumen ejecutivo

Se agrego un `Opportunity Quality Firewall` deterministico antes del scoring final para cortar ruido de Reddit, contenido SEO, ficcion, conspiraciones, discusiones sin comprador, duplicados, scams y presupuestos mal interpretados.

El objetivo del cambio es privilegiar pocas oportunidades accionables sobre mucho volumen ruidoso. Reddit sigue existiendo como fuente secundaria, pero ya no puede producir A1/A2 ni cuentas calientes sin comprador verificable, evidencia comercial y riesgo controlado.

## Problema observado

- `nosleep` y `conspiracy` podian entrar como oportunidades por mencionar tecnologias, dinero o palabras como API/AI.
- Posts SEO tipo `How to Choose...` parecian leads aunque eran contenido.
- `Could you please rate my proposal?` era una discusion, no un comprador.
- Numeros como `30+ jobs`, `3-6 months` y `10-15%` podian contaminar presupuestos.
- Cuentas calientes podian inflarse por basura agregada de Reddit.

## Pipeline nuevo

```txt
RAW_INGESTED
  -> PARSED
  -> SOURCE_POLICY_CHECKED
  -> COMMERCIAL_INTENT_CHECKED
  -> SCAM_RISK_CHECKED
  -> BUDGET_NORMALIZED
  -> BUYER_IDENTITY_CHECKED
  -> DUPLICATE_CLUSTERED
  -> QUALITY_SCORED
  -> ROUTED_TO_STAGE
```

Estados de calidad:

```txt
READY_TO_CONTACT
WATCHLIST
REVIEW_REQUIRED
LOW_VALUE
SUSPECT
REJECTED_NOISE
DUPLICATE
```

## Archivos principales modificados

- `scrapperlanas/services/quality.py`: firewall, parser de presupuesto, intencion comercial, riesgo, buyer identity, trust score y duplicados.
- `scrapperlanas/services/ingestion.py`: extraccion de presupuesto delegada al parser robusto.
- `scrapperlanas/services/scoring.py`: scoring legacy capado por calidad.
- `scrapperlanas/services/intelligence/scoring.py`: Score V2 respeta hard caps de calidad.
- `scrapperlanas/services/intelligence/next_best_action.py`: el bot bloquea contacto cuando la calidad no lo permite.
- `scrapperlanas/services/intelligence/outreach.py`: no redacta outreach directo para oportunidades no contactables.
- `scrapperlanas/services/pipeline.py`: calcula calidad, guarda metadata, enruta estado inicial y evita cuentas calientes basura.
- `scrapperlanas/services/buyer_intelligence.py`: no agrupa ruido o descartados como cuentas calientes.
- `scrapperlanas/views.py`: filtros, metricas, CSV y serializacion de calidad.
- `scrapperlanas/templates/dashboard.html`: chips de calidad, vistas rapidas y panel de "por que no contactar".
- `scrapperlanas/db.py`: configuracion default de Reddit allowlist/greylist/blocklist y quality config.
- `tests/test_quality_firewall.py`: fixtures obligatorios del firewall.

## Reglas de Reddit

Reddit queda dividido en:

- Allowlist: `forhire`, `freelance_forhire`, `remotework`, `remotejobs`, `remotepython`, `hireaprogrammer`, `jobs4bitcoins`, `slavelabour`, `webdev`, `smallbusiness`, `entrepreneur`, `SaaS`, `startups`, `n8n`, `automation`, `nocode`.
- Greylist: `Python`, `django`, `flask`, `reactjs`, `node`, `programming`, `learnprogramming`, `datascience`, `MachineLearning`, `artificial`, `LocalLLaMA`, `webscraping`.
- Blocklist: `nosleep`, `conspiracy`, `AskReddit`, `AmItheAsshole`, `relationship_advice`, `worldnews`, `news`, `politics`, `memes`, `funny`, `gaming`, `teenagers`, `NoStupidQuestions`, `Showerthoughts`, `WritingPrompts`, `creepypasta`, `UnresolvedMysteries`, `UFOs`, `aliens`.

Para blocklist:

- `qualityStage = REJECTED_NOISE`
- `score_tier = D`
- `commercial_status = ignored`
- no buyer account
- no cuenta caliente
- razon: `blocked_subreddit`

## Intencion comercial

Labels deterministicas:

- `DIRECT_HIRING`
- `PROCUREMENT`
- `RFP`
- `BUYER_PAIN`
- `SERVICE_REQUEST`
- `MARKET_RESEARCH`
- `JOB_AGGREGATOR`
- `SEO_CONTENT`
- `DISCUSSION`
- `FICTION`
- `CONSPIRACY`
- `NEWS`
- `SELF_PROMO`
- `UNKNOWN`

Reglas clave:

- `review my proposal` y `rate my proposal` no son leads.
- `how to choose`, `guide`, `tutorial`, `case study` son contenido SEO si no hay comprador.
- Ficcion, conspiracion, noticias y discusiones quedan rechazadas o low value.
- Reddit username o subreddit no bastan como comprador A1/A2.

## Presupuesto

Nuevo contrato:

```txt
amountMin
amountMax
currency
unit: hour | project | month | year | event | unknown
confidence
rawEvidence
isEstimated
isValidCommercialBudget
```

No se interpreta como presupuesto:

- `30+ jobs`
- `3-6 months`
- `20 minutes`
- `30 total`
- anos
- porcentajes
- comisiones como `10-15%`

Si hay contexto comercial explicito, si se acepta:

- `Presupuesto USD 2400`
- `Presupuesto entre 1800 y 3200 USD`
- `Budget $4,800`
- `$200 for 3 hour events`

## Riesgo

El risk engine penaliza:

- `commission only`
- `serious side income`
- `weekly bonus`
- `no experience required`
- `DM me for details`
- `telegram only`
- `whatsapp only`
- `crypto payment`
- `free test`
- `send your ID`
- `bank account`
- `forex`
- `casino`
- `essay writing`
- `fake reviews`

Riesgo `HIGH` o `CRITICAL` impide A1/A2. `CRITICAL` queda como `SUSPECT` o `REJECTED_NOISE` segun la causa.

## Hard caps del score

- `blocked_subreddit`: score <= 10, grade D/REJECTED.
- `FICTION`, `CONSPIRACY`, `NEWS`, `DISCUSSION`: score <= 20.
- `SEO_CONTENT`: score <= 35.
- `buyerIdentity.confidence < 30`: score <= 45.
- presupuesto invalido: maximo B.
- `riskLevel = HIGH`: maximo C.
- `riskLevel = CRITICAL`: rechazado o sospechoso.
- duplicado exacto: se oculta como `DUPLICATE`.
- job aggregator: `WATCHLIST`, no `READY_TO_CONTACT`.

## UI y CSV

El Inbox ahora muestra chips:

- Fuente
- Intencion
- Riesgo
- Comprador
- Presupuesto
- Estado de calidad
- Duplicados ocultos

Vistas rapidas agregadas:

- Solo contactables
- Revision necesaria
- Sospechosos
- Ruido rechazado
- Duplicados
- Reddit confiable
- Reddit oculto
- Presupuesto dudoso
- Sin comprador

La vista principal oculta por defecto `REJECTED_NOISE`, `DUPLICATE` y `LOW_VALUE`. El CSV agrega columnas de calidad y respeta esa misma visibilidad salvo que se use un filtro explicito.

Columnas CSV nuevas:

```txt
qualityStage
commercialIntentLabel
commercialIntentScore
riskLevel
riskScore
riskReasons
buyerConfidence
budgetConfidence
sourceTrustScore
rejectionReasons
duplicateClusterId
duplicateCount
isContactable
```

## Copiloto comercial

Antes de redactar outreach:

- Si `qualityStage != READY_TO_CONTACT`, responde "No recomiendo contactar esta oportunidad todavia".
- Explica razones y evidencia faltante.
- No genera pitch directo para Reddit bloqueado, sospechoso, low value o sin comprador suficiente.
- Para `READY_TO_CONTACT`, puede redactar usando dolores, skills, evidencia, fuente, presupuesto y deadline reales.

## Casos validados

- `nosleep - The Warehouse I Work At Has a Basement That Doesn't Exist...`
  - `REJECTED_NOISE`, `FICTION`, `blocked_subreddit`.
- `conspiracy - Alisa Valdes-Rodriguez...`
  - `REJECTED_NOISE`, `CONSPIRACY`, `blocked_subreddit`, sin cuenta caliente.
- `How to Choose an Enterprise AI Consultant...`
  - `SEO_CONTENT`, `LOW_VALUE`, no contactable.
- `Could you please rate my proposal?`
  - `DISCUSSION`, no contactable.
- `[US] 30+ Developer jobs that opened this week`
  - `JOB_AGGREGATOR`, `WATCHLIST`, sin presupuesto.
- `3-6 months`
  - no presupuesto.
- `10-15% rate`
  - comision/riesgo, no presupuesto.
- `$200 for 3 hour events`
  - presupuesto valido, unidad `event`.
- Reddit `[Hiring] automation specialist $50/hour` sin comprador verificable
  - no A1/A2 automatico.

## Como ajustar allowlist/blocklist

La configuracion default vive en `DEFAULT_REDDIT_POLICY_CONFIG` dentro de `scrapperlanas/db.py`:

```json
{
  "reddit": {
    "enabled": true,
    "defaultVisibility": "review_required",
    "allowlist": [],
    "greylist": [],
    "blocklist": [],
    "minCommercialIntentScore": 65,
    "hideRejectedByDefault": true,
    "maxResultsPerRun": 50
  },
  "quality": {
    "minReadyToContactScore": 75,
    "minBuyerConfidence": 50,
    "rejectCriticalRisk": true,
    "hideDuplicates": true
  }
}
```

En instalaciones existentes se puede editar `source_policies.config_json` para la fuente `reddit` desde la base o desde una UI de settings futura.

## Pruebas ejecutadas

```powershell
python -m compileall -q scrapperlanas tests
python -m pytest tests/test_quality_firewall.py tests/test_intelligence_v2.py tests/test_scoring.py tests/test_app.py -q
python -m pytest -q
pip-audit -r requirements.txt
pip-audit -r requirements-dev.txt
docker compose config --quiet
```

Resultado:

```txt
91 passed
No known vulnerabilities found
docker compose config --quiet no valido porque falta POSTGRES_PASSWORD en el entorno local.
```

La suite completa incluye smoke visual con Playwright/Chromium.

## Riesgos pendientes

- Reddit Public JSON sigue dependiendo de endpoints publicos; en produccion puede requerir integracion autenticada o proxy controlado.
- El trust score de autor/dominio es deterministico y local; una version futura puede guardar historial real por autor/dominio.
- Near-duplicate clustering usa llaves y similitud deterministica; embeddings opcionales podrian mejorar clusters semanticos.
- La edicion de allowlist/greylist/blocklist todavia no tiene pantalla dedicada.

## Como correr local

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python run.py
```

Abrir:

```txt
http://127.0.0.1:5000
```
