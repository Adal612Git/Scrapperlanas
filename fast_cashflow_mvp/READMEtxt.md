# Fast-Cashflow Engine (MVP)

Un scraper resiliente que detecta oportunidades de freelance en Reddit, las puntúa por potencial de cashflow y te alerta por Telegram. Guarda histórico en SQLite.

## Qué hace exactamente
- Descarga posts recientes de `r/forhire` y `r/freelance_forhire`.
- Filtra rápido por keywords (scoring base).
- Usa Ollama local para extraer título, precio, stack y score AI.
- Fusiona scores y dispara alertas cuando supera el umbral.
- Guarda cada oportunidad en `opportunities.db`.

## Cómo correrlo
1. Instalar dependencias:
   - `python -m pip install -r requirements.txt`
2. Configurar `.env`:
   - Copiar `.env.example` a `.env` y completar:
     - `TELEGRAM_BOT_TOKEN`
     - `TELEGRAM_CHAT_ID`
     - `OLLAMA_HOST=http://localhost:11434`
     - `OLLAMA_MODEL=mistral`
3. Levantar Ollama:
   - `ollama serve`
   - `ollama pull mistral`
4. Ejecutar:
   - `python main.py`
   - `python main.py --loop` (cada 5 minutos)

## Dónde ver resultados
- **Telegram**: cada alerta llega como mensaje “FAST CASHFLOW ALERT”.
- **Base de datos**: `opportunities.db`.
- **Logs**: `runtime.log`.
- **Reporte HTML**: `report.html` (alertas + basura + fuentes).

Consulta rápida a la DB:
```powershell
python - << 'PY'
import sqlite3
conn = sqlite3.connect("opportunities.db")
rows = conn.execute("SELECT title, score, url, created_at FROM jobs ORDER BY created_at DESC LIMIT 10").fetchall()
for r in rows:
    print(r)
PY
```

## Cómo usar esas alertas (workflow de acción)
1. **Filtrado rápido**: si no hay precio o el stack no te sirve, descarta.
2. **Validar urgencia**: prioriza posts con “ASAP”, “urgent”, “fix”.
3. **Responder en menos de 10 minutos**:
   - Mensaje corto, directo, con 1 resultado similar y propuesta concreta.
4. **Registrar contacto**: guarda el link y estado (en una nota o spreadsheet).

## Diagnóstico de problemas comunes
- **`Ollama error: Expecting ',' delimiter...`**
  - Ollama devolvió JSON inválido. El sistema sigue, pero ese post pierde AI score.
  - Solución: agregar un reparador de JSON + reintento (pendiente si lo pedís).
- **`database is locked`**
  - Ocurre si hay dos procesos usando la DB.
  - Solución: cerrar otros `python main.py` (ya se agregó WAL + retry).

## Estado actual
- Ingesta: OK
- Scoring base: OK
- Ollama: OK (con fallos de JSON ocasionales)
- Alertas: OK si Telegram está configurado
- Persistencia: OK

## Próximas mejoras (prioridad baja, alto impacto)
1. Reparación y validación robusta de JSON AI.
2. Filtro “FOR HIRE” vs “HIRING” para evitar alerts no deseadas.
3. Exportar resultados a HTML/CSV con ranking.
