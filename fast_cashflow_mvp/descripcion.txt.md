Antes que nada: vi tus archivos del MVP y ya hay “material de producto” bien sólido (README, informe final, reporte HTML, logs y el agent spec). Nota rápida: a veces los archivos que se suben aquí pueden **expirar**; si luego quieres que yo cite/extraiga cosas exactas de *otros* archivos que ya no aparezcan, solo re-súbelos y listo. 🪷

Abajo te dejo el **documento completito** en **Markdown**, listo para copiar/pegar a **NotebookLM** como si fuera un entregable oficial del Loto.

---

# HUNTER — Agente Especial de las Fuerzas del Loto

### *Sistema de Caza de Oportunidades (Cashflow Intelligence Engine)*

**Producto:** Loto | Inteligencia Operativa
**Versión:** v1.0 (MVP operacional)
**Fecha:** 2026-02-08
**Estado:** Funcional, listo para operación diaria

---

## 0) Resumen Ejecutivo

**HUNTER** es un agente especial del Loto diseñado para un solo propósito: **encontrar oportunidades reales, recientes y accionables** en internet (freelance, gigs, “quick wins”), **priorizarlas**, y **avisar en tiempo real** para que el operador cierre rápido.

En lugar de “buscar trabajo”, HUNTER convierte el caos de publicaciones en una **cola priorizada**:

* detecta oportunidades,
* filtra ruido,
* puntúa por potencial de cashflow,
* deduplica spam/repetidos,
* alerta por Telegram,
* guarda evidencia e historial en SQLite,
* genera un reporte HTML para auditoría y análisis.

**Principio Loto:** *No se trata de mirar mil cosas. Se trata de encontrar 3 que pagan.* 🪷🎯

---

## 1) El Problema (y por qué HUNTER existe)

En fuentes como Reddit, foros y tablones, el volumen es alto y la señal es baja. El operador se quema en:

* desplazamiento infinito,
* posts duplicados o basura,
* ofertas sin presupuesto,
* oportunidades que caducan por tardanza.

**La realidad operativa:** el que responde primero y claro, gana.
**El cuello de botella:** detectar + priorizar + actuar **en minutos**, no en horas.

HUNTER reduce el tiempo entre:

> **“Existe una oportunidad” → “Ya la estoy atacando”**

---

## 2) Qué es HUNTER (definición de producto)

HUNTER es un **motor de inteligencia operativa** que hace tres cosas en ciclo:

1. **Ingesta:** consulta fuentes (por ahora, Reddit) y recolecta items recientes.
2. **Análisis + Scoring:** aplica heurísticas (keywords) y, cuando conviene, usa IA local (Ollama) para extraer campos útiles.
3. **Acción inmediata:** alerta por Telegram si el score supera el umbral; guarda todo en DB; genera reporte.

**Salida final:** una lista priorizada de oportunidades + evidencia + trazabilidad.

---

## 3) Qué hace exactamente (funcionalidad)

### 3.1 Fuentes iniciales (MVP)

En el MVP actual, HUNTER consulta endpoints JSON de Reddit (sin APIs pagadas), incluyendo:

* `r/forhire` (posts recientes)
* `r/freelance_forhire` (posts recientes)
* búsquedas por keyword dentro de `r/forhire` (ej. python, scraping, bot, automation, .net, c#)

Esto permite una cobertura razonable sin necesidad de navegador automatizado.

### 3.2 Pipeline operativo (end-to-end)

1. **Descarga** items recientes.
2. **Normaliza** texto (título + extracto + metadata).
3. **Score base (rápido):** detecta keywords y señales (urgencia, “fix”, “ASAP”, etc.).
4. **IA local (Ollama) cuando aplica:** extrae y estructura:

   * presupuesto/precio,
   * stack,
   * tipo de trabajo,
   * “match” con tus capacidades,
   * score AI complementario.
5. **Deduplicación:** evita alertar por posts repetidos o demasiado similares.
6. **Persistencia:** guarda en SQLite (`opportunities.db`).
7. **Alertas:** manda Telegram en tiempo real si supera umbral.
8. **Reporte HTML:** genera `report.html` con:

   * alertas,
   * descartes (basura) con razón,
   * lista de fuentes consultadas.

---

## 4) Evidencia de ejecución real (MVP probado)

En una ejecución documentada del ciclo, el sistema generó un reporte con métricas claras:

* **Generado:** 2026-02-08 10:09:38Z
* **Fuentes consultadas:** 8
* **Items analizados:** 70
* **Alertas:** 33

Ejemplos reales detectados (muestras):

* “Systems & Web Builder for Practical Solutions” (quick win ~$100)
* “AI Sales Automation Engineer” (~$30/hr, n8n + agentic workflows)
* “Work from Home Opportunity…” (~$20/hr)

También se descartó basura con razones típicas:

* `no_keywords` (no coincide con objetivos)
* `score_below_threshold` (no vale el tiempo)

**Conclusión operativa:** HUNTER ya funciona como radar y filtro. El cierre sigue siendo humano (por diseño). 🛰️

---

## 5) Propuesta de Valor (por qué esto es un “producto del Loto”)

### Beneficios directos

* **Ahorro de tiempo:** menos doomscrolling, más acción.
* **Velocidad:** te enteras primero, respondes primero.
* **Prioridad automática:** te entrega “qué vale atacar” antes de que pienses.
* **Memoria y auditoría:** DB + reporte para aprender qué convierte.
* **Operación modular:** agregar fuentes y reglas sin reescribir todo.

### Beneficios estratégicos (Loto)

* Convierte la búsqueda de ingresos en un **sistema repetible**.
* Permite iterar: “qué keywords convierten”, “qué tipo de post paga”, etc.
* Se vuelve una pieza de infraestructura: **Inteligencia de Cashflow**.

---

## 6) Perfil del Usuario Objetivo

* Operador freelance (dev, automatización, scraping, bots, .NET/C#).
* Founder que necesita cashflow rápido sin perder el foco del producto principal.
* Equipo pequeño que busca “gigs” para financiar desarrollo.
* Cazadores de leads que trabajan con respuesta rápida.

---

## 7) Concepto del Agente (narrativa Loto)

HUNTER no es “un script”. Es un agente con doctrina:

* **Doctrina 1:** *La oportunidad es un fenómeno breve; si la dudas, ya se fue.*
* **Doctrina 2:** *La prioridad no la decide el ego, la decide el score.*
* **Doctrina 3:** *El sistema no cierra por ti; te pone frente a la puerta correcta.*
* **Doctrina 4:** *Si no hay evidencia, no existe: logs, DB, reportes.*

---

## 8) Arquitectura Técnica

### 8.1 Componentes (visión general)

* **Ingestion Layer**

  * Cliente HTTP resiliente (retries, backoff, user-agent rotation si aplica).
  * Adaptadores por fuente (Reddit hoy; extensible mañana).
* **Analysis Layer**

  * Normalización de texto.
  * Scoring heurístico (keywords/señales).
  * IA local (Ollama) para extracción estructurada cuando se requiere.
* **Decision Layer**

  * Fusión de scores (base + AI).
  * Umbral para alertas.
  * Deduplicación por similitud (RapidFuzz).
* **Output Layer**

  * Telegram alerting.
  * SQLite persistence.
  * HTML report generator.
  * Runtime logs.

### 8.2 Diagrama (Mermaid)

```mermaid
flowchart TD
  A[Fuentes: Reddit + búsquedas] --> B[Ingesta HTTP Resiliente]
  B --> C[Normalización + Extracción básica]
  C --> D[Score Base: Keywords/Señales]
  C --> E[IA Local (Ollama): extracción estructurada]
  D --> F[Fusión de score]
  E --> F
  F --> G{Score >= Umbral?}
  F --> H[Deduplicación (RapidFuzz)]
  H --> G
  G -- Sí --> I[Alerta Telegram]
  G -- No --> J[Descartes / Basura]
  I --> K[SQLite: opportunities.db]
  J --> K
  K --> L[Reporte HTML: report.html]
  K --> M[Logs: runtime.log]
```

---

## 9) Stack Tecnológico

### Dependencias clave (MVP)

* `aiohttp` — HTTP async
* `python-dotenv` — configuración `.env`
* `rapidfuzz` — deduplicación por similitud
* `loguru` — logging
* `ollama` — interfaz con IA local

### Infra recomendada

* Windows / Linux (local) o VPS.
* Ollama local corriendo en `localhost:11434` (o donde definas).

---

## 10) Instalación y Configuración (modo operador)

### 10.1 Requisitos

1. Python 3.10+ recomendado
2. Dependencias instaladas
3. (Opcional pero recomendado) Ollama instalado y corriendo
4. Bot de Telegram y chat id configurados

### 10.2 Variables de entorno (`.env`)

* `TELEGRAM_BOT_TOKEN`
* `TELEGRAM_CHAT_ID`
* `OLLAMA_HOST` (por defecto `http://localhost:11434`)
* `OLLAMA_MODEL` (ej. `mistral`)
* Keywords objetivo (si está implementado en tu versión)

### 10.3 Modo de operación

* **One-shot:** corre una vez, genera reporte y termina.
* **Loop:** corre cada X minutos (ej. 5 min) para operar como radar continuo.

### 10.4 Salidas del sistema (artefactos)

* `opportunities.db` — historial y evidencia
* `runtime.log` — trazabilidad y diagnóstico
* `report.html` — tablero de alertas + descartes + fuentes

---

## 11) Interpretación del Score (doctrina operativa)

El score total combina:

* **Score base:** keywords + señales de urgencia/encaje.
* **Score AI (si aplica):** presupuesto, stack, urgencia, match.

Interpretación recomendada:

* **0–2:** ruido / baja prioridad
* **3–5:** oportunidad válida, vale responder
* **6+:** prioridad máxima (atacar ya)

---

## 12) Workflow humano recomendado (cómo convertir alertas en dinero)

HUNTER te entrega objetivos. El operador ejecuta el cierre.

### 12.1 Regla de oro: responder en menos de 10 minutos

Cuando llega alerta:

1. abrir link
2. validar presupuesto/stack
3. responder corto y directo
4. proponer siguiente paso (call o “I can start now”)

### 12.2 Plantilla de respuesta rápida (copiable)

**Versión “quirúrgica” (Reddit):**

* 1 línea: confirmo que entiendo el problema
* 1 línea: prueba social (algo similar que ya hice)
* 1 línea: propuesta concreta + tiempo
* 1 línea: pregunta cerrada para iniciar

Ejemplo:

> Hi! I can build/fix this today. I’ve shipped similar automation/scraping workflows recently (fast + reliable). If you share the exact requirements + current setup, I can deliver a first working version in X hours. Want me to start now?

---

## 13) Seguridad, Cumplimiento y Buenas Prácticas

HUNTER está diseñado para operar de forma responsable:

* **Respeta límites:** rate limiting y backoff para no saturar fuentes.
* **Evita datos sensibles:** almacenar lo mínimo necesario (título/url/score).
* **Auditoría total:** logs + DB + report para rastrear decisiones.
* **No requiere APIs pagadas:** reduce dependencia externa.

**Nota:** cada fuente tiene reglas (ToS). La configuración debe mantenerse dentro de límites razonables y éticos.

---

## 14) Diagnóstico de Problemas Comunes (modo “no me deja avanzar”)

### 14.1 Error: “Cannot connect to host localhost:11434…”

Significa que **Ollama no está corriendo**, el puerto está bloqueado, o el host no coincide.

Checklist rápido:

1. `ollama serve` activo
2. modelo descargado: `ollama pull mistral` (o el que uses)
3. `OLLAMA_HOST` correcto en `.env`
4. firewall/antivirus no bloquea 11434

### 14.2 “Se tarda muchísimo, parece que no termina”

Normal: si amplías fuentes y analizas muchos items, cada llamada a IA suma tiempo.

Soluciones:

* bajar cantidad de items por ciclo,
* usar IA solo para items prefiltrados,
* ejecutar en loop con intervalos,
* cachear resultados.

---

## 15) Roadmap (evolución del producto)

### v1.1 (alto impacto, bajo riesgo)

* Reparación robusta de JSON si el modelo devuelve formato imperfecto.
* Mejor filtro “FOR HIRE” vs “HIRING”.
* Export CSV/JSON para análisis externo.

### v1.2 (expansión de fuentes)

* Upwork RSS/Feeds donde sea permitido
* PeoplePerHour / otras bolsas (con reglas claras)
* Fuentes locales (GDL): directorios y sitios públicos (si aplica)

### v2.0 (Hunter como sistema de inteligencia)

* Panel web (dashboard) + ranking histórico
* “Playbooks” por tipo de oportunidad (scripts de cierre)
* Métricas: tasa de respuesta, tasa de cierre, ticket promedio
* Auto-tagging por categoría: “quick win”, “retainer”, “alto valor”

---

## 16) Métricas de Éxito (KPIs)

HUNTER no se mide por “cuántos posts ve”, sino por:

* **Time-to-first-reply** (minutos)
* **Alerts útiles / día**
* **% de alerts que respondes**
* **% de respuestas que avanzan** (call / negociación)
* **Cierres / semana**
* **$ generado / semana**

---

## 17) Entregable y posicionamiento comercial

**HUNTER** se presenta como:

> “Agente de inteligencia operativa del Loto para cazar oportunidades de cashflow en tiempo real.”

**Qué compra el usuario (interno o externo):**

* Setup + configuración de keywords y umbrales
* Entrenamiento de workflow humano (plantillas + disciplina)
* Mantenimiento y expansión de fuentes
* Panel/reportes para mejora continua

---

## 18) Cierre

HUNTER ya cumple la misión del MVP:

* **detecta oportunidades reales**,
* las **prioriza**,
* **alerta**,
* y deja evidencia para operar como sistema.

Lo pendiente no es técnico, es estratégico:
**convertir velocidad en cierres.** 🪷⚔️

---

### Anexo A — “Qué meter a NotebookLM”

Para que NotebookLM te lo deje fino como presentación/producto, sube:

* este documento (Hunter Spec)
* `README.md`
* `FINAL_REPORT.md`
* `report.html` (evidencia visual)
* `runtime.log` (diagnóstico y trazabilidad)

---

Si quieres, también puedo:

1. convertir este doc a **PDF** con portada Loto (bonito, para venderlo), o
2. reescribirlo en modo “brochure comercial” (más corto, más marketinero) sin perder la parte técnica.
