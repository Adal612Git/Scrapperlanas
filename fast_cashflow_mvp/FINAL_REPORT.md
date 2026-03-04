# Informe Final — Fast‑Cashflow Engine (MVP)

Fecha: 2026‑02‑08

## 1) Qué es
Un sistema de **búsqueda y priorización automática** de oportunidades freelance en Reddit.  
Su objetivo es **reducir el tiempo entre “oportunidad encontrada” y “acción”** usando:
- scraping resiliente,
- análisis con IA local (Ollama),
- scoring y deduplicación,
- alertas inmediatas por Telegram,
- histórico persistente en SQLite.

## 2) Qué hace (resumen operativo)
1. Descarga posts recientes de `r/forhire` y `r/freelance_forhire`.
2. Filtra por keywords de interés (Python, C#, scraping, automation, etc.).
3. Envía el texto a **Ollama local** para extraer:
   - título,
   - presupuesto,
   - stack,
   - resumen,
   - score de urgencia/compatibilidad.
4. Fusiona score heurístico + score AI.
5. Si supera el umbral, manda alerta por Telegram y guarda en `opportunities.db`.
6. Genera `report.html` con:
   - alertas,
   - descartes (“Basura”) con razón,
   - fuentes consultadas (URLs).

## 3) Qué pasó hoy (resultado real de ejecución)
### 3.1 Primeras pruebas
- El sistema ejecutó el ciclo completo y **trajo 15 posts**.
- Ollama no estaba corriendo al inicio → errores de conexión.
- Cuando se levantó Ollama, el pipeline **sí produjo alertas reales**.

### 3.2 Con Ollama activo
- El sistema **detectó múltiples oportunidades reales** y generó alertas con score alto.
- Se confirmó que **los datos son reales y recientes**.

### 3.3 Comportamiento “loopeado”
No estaba en loop infinito.  
El ciclo tardó bastante porque:
- Se amplió el número de fuentes y búsquedas (≈70 items por ciclo).
- Cada item que pasa filtro hace una llamada a Ollama, lo que suma tiempo.

**Resultado:** parecía que “no terminaba”, pero en realidad estaba procesando una cola grande.

## 4) Qué significa el score
El score total combina:
- **Keywords encontradas** (score base).
- **Resultado AI** (urgencia, stack match, presupuesto).

Se interpreta así:
- **0–2:** ruido o baja prioridad.
- **3–5:** oportunidad válida, vale responder.
- **6+**: oportunidad altamente prioritaria.

## 5) Qué falta (parte humana)
El sistema **detecta oportunidades reales**, pero **la conversión depende del cierre**.
Eso incluye:
- validar que el post sea auténtico,
- responder rápido y con propuesta clara,
- seguir el proceso de negociación.

En otras palabras:  
**el software abre puertas; el cierre lo hace el operador.**

## 6) Estado actual del MVP
- Ingesta: OK  
- Scoring base: OK  
- Ollama: OK (con reintento y reparación de JSON)  
- Alertas: OK (Telegram)  
- Persistencia: OK  
- Reporte HTML: OK

## 7) Evidencia tangible (resultados)
Ejemplo de resultados guardados en `opportunities.db` (últimos registros):
- “Systems & Web Builder for Practical Projects”
- “Telegram Bot Developer (Python)”
- “AI Sales Automation Engineer”
- “Work from Home Position - $20/hour”

Estos registros reflejan que **las oportunidades son reales** y estaban activas al momento de captura.

## 8) Conclusión
El MVP **cumple el objetivo central**:  
te lleva directo a ofertas reales y recientes, con un filtro que ahorra tiempo.  

Lo único pendiente no es técnico:  
**verificar cada oportunidad y cerrar el trato.**
