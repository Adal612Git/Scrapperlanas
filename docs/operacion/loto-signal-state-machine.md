# Loto Signal State Machine

## Objetivo

La maquina de estados evita cambios comerciales incoherentes y deja reglas claras para automatizacion, UI y API.

## Estados Canonicos

- `new`
- `triaged`
- `interesting`
- `outreach_drafted`
- `contacted`
- `replied`
- `proposal_drafted`
- `proposal_sent`
- `follow_up_due`
- `won`
- `lost`
- `suspicious`
- `discarded`
- `archived`

## Transiciones Permitidas

```text
new -> triaged | interesting | suspicious | discarded
triaged -> interesting | discarded | suspicious
interesting -> outreach_drafted | contacted | discarded
outreach_drafted -> contacted | discarded
contacted -> replied | follow_up_due | lost
replied -> proposal_drafted | proposal_sent | lost
proposal_drafted -> proposal_sent | discarded
proposal_sent -> follow_up_due | won | lost
follow_up_due -> contacted | replied | won | lost | discarded
suspicious -> discarded | archived | interesting
discarded -> archived | interesting
won -> archived
lost -> archived
```

## Guards

- `won` requiere evidencia, valor ganado o confirmacion manual.
- `proposal_sent` requiere propuesta y canal (`url`, `apply_url`, `action_url` o `contact_channel`).
- `suspicious -> interesting` requiere `reviewed_by_user` u override manual con razon.
- `discarded -> interesting` requiere `restored_by_user` u override manual con razon.
- `follow_up_due` requiere contacto previo o estar saliendo de `contacted`.
- Estados bloqueados no pueden contactarse directamente.
- Override manual exige `reason`.

## API

```text
POST /api/opportunities/<id>/transition
```

Payload:

```json
{
  "next_state": "contacted",
  "reason": "Contacto preparado por el operador",
  "override": false,
  "context": {}
}
```

Respuesta exitosa:

```json
{
  "ok": true,
  "transition": {
    "allowed": true,
    "previous_state": "interesting",
    "next_state": "contacted",
    "guard_result": "ok"
  },
  "legacy_state": "APLICADO"
}
```

Si una transicion falla, la API responde `409` con `guard_result`.
