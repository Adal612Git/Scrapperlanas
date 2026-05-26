# Loto Signal Intelligence V2

## Objetivo

Intelligence V2 convierte oportunidades crudas en decisiones comerciales explicables: prioridad, evidencia, riesgos, siguiente accion y borrador inicial.

## Modulos

- `schemas.py`: dataclasses con `to_dict()`.
- `utils.py`: helpers deterministas.
- `evidence.py`: evidencia humana.
- `risk.py`: advertencias y `scam_risk`.
- `scoring.py`: score A1/A2/B/C/D.
- `next_best_action.py`: accion recomendada.
- `deduplication.py`: similitud entre cuentas.
- `outreach.py`: composer V2.
- `state_machine.py`: pipeline canonico.

## Score V2

Inputs soportados:

- presupuesto y moneda
- dominio/comprador
- fuente y pais
- deadline
- titulo/descripcion/raw text
- skills
- dolores
- contactos
- evidencia
- estado
- interacciones historicas cuando existan
- duplicado/riesgo/target match cuando existan

Output:

```json
{
  "grade": "A1",
  "numeric_score": 94,
  "components": {
    "money_score": 100,
    "fit_score": 88,
    "urgency_score": 95,
    "contactability_score": 59,
    "confidence_score": 72,
    "risk_penalty": 0
  },
  "reasons": [],
  "warnings": [],
  "suggested_next_action": "Redactar mensaje"
}
```

## Next Best Action

Tipos:

- `REDACT_OUTREACH`
- `INVESTIGATE_CONTACT`
- `CREATE_FOLLOW_UP`
- `SEND_FOLLOW_UP`
- `PREPARE_PROPOSAL`
- `REVIEW_DUPLICATE`
- `REVIEW_RISK`
- `DISCARD_LOW_VALUE`
- `MARK_WON`
- `WAIT`

Reglas base:

- A1/A2 sin contacto y canal disponible -> redactar mensaje.
- A1/A2 sin contacto y baja contactabilidad -> investigar contacto.
- contactado sin respuesta por 3 dias -> enviar follow-up.
- respondido con presupuesto -> preparar propuesta.
- duplicado fuerte -> revisar duplicado.
- riesgo alto -> revisar riesgo.
- deadline vencido sin contacto -> descartar o esperar.

## Outreach Composer

Tonos:

- consultivo
- directo
- tecnico
- procurement
- GitHub tecnico
- freelance breve
- seguimiento
- cierre educado
- propuesta corta

Reglas:

- usa comprador, titulo, dolores, skills, evidencia, presupuesto y deadline reales
- no inventa credenciales
- no promete resultados imposibles
- mantiene tono humano y profesional

## Risk Engine

Detecta:

- senales de scam
- texto generico
- comprador sin identidad clara
- deadline vencido
- baja contactabilidad
- posible duplicado
- poca evidencia textual
- riesgo heredado de fuente

## Evidence Engine

Genera explicaciones como:

- presupuesto visible o estimado
- deadline extraido
- dolor detectado
- skill match
- contacto o dominio disponible
- riesgo heredado
- evidencia textual trazable

## Dedupe

Compara:

- dominio normalizado
- nombre de comprador
- pais
- contactos
- overlap de skills
- overlap de dolores

Output:

```json
{
  "duplicate_confidence": 0.91,
  "recommended_action": "merge",
  "reasons": ["Dominio normalizado identico."],
  "conflicting_fields": []
}
```
