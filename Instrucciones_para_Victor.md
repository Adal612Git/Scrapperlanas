# Instrucciones para Victor

Victor: eres un super agente de alta elite. Este documento es tu manual operativo tipo militar para desplegar, operar y mantener Hunter desde cero.

**Mision**
Detectar oportunidades de trabajo freelance de alto valor y baja friccion, reaccionar rapido y convertir alertas en ingresos, sin detener el funcionamiento de Loto.

**Que es Hunter**
Hunter es un sistema automatizado que:
1. Extrae posts recientes de Reddit (r/forhire, r/freelance_forhire y busquedas por keywords).
2. Aplica un filtro rapido por keywords.
3. Usa Ollama local para extraer titulo, precio, stack y score AI.
4. Suma el score y decide si enviar alerta.
5. Registra historial en SQLite para evitar duplicados.
6. Genera un reporte HTML de cada ciclo.

**Reglas de combate**
1. No usar APIs pagas.
2. Mantener costo y complejidad bajos.
3. Operar con rapidez, estabilidad y trazabilidad.
4. Proteger secretos (.env) y no compartirlos.

**Mapa del sistema**
- Codigo principal: `fast_cashflow_mvp\main.py`
- Configuracion: `fast_cashflow_mvp\config.py`
- Variables de entorno: `fast_cashflow_mvp\.env`
- Dependencias: `fast_cashflow_mvp\requirements.txt`
- Logs: `fast_cashflow_mvp\runtime.log`
- Base de datos: `fast_cashflow_mvp\opportunities.db`
- Reporte HTML: `fast_cashflow_mvp\report.html`

**Alta desde cero (ZIP, Windows)**
1. Descomprime el ZIP en una carpeta nueva.
2. Abre PowerShell y entra a la carpeta del proyecto.
3. Verifica Python:
```powershell
python --version
```
4. Crea y activa entorno virtual:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
5. Instala dependencias:
```powershell
python -m pip install -r requirements.txt
```
6. Crea el archivo `.env` desde `.env.example` y completa valores reales:
```powershell
Copy-Item .env.example .env
```
7. Instala Ollama (si no existe) y levanta el servicio:
```powershell
ollama serve
```
8. Descarga un modelo (recomendado por defecto):
```powershell
ollama pull mistral
```
9. Ejecuta un ciclo de prueba:
```powershell
python main.py
```
10. Ejecuta en modo continuo (cada 5 minutos):
```powershell
python main.py --loop
```

**Variables de entorno (archivo .env)**
1. `TELEGRAM_BOT_TOKEN` = token del bot.
2. `TELEGRAM_CHAT_ID` = chat donde llegarana las alertas.
3. `OLLAMA_HOST` = por defecto `http://localhost:11434`.
4. `OLLAMA_MODEL` = modelo de Ollama (ej: `mistral`).
5. `TARGET_KEYWORDS` = lista separada por comas (ej: `python,scraping,bot,automation,.net,c#,fix,urgent,bug`).

**Telegram: alta del bot**
1. En Telegram, abre `@BotFather`.
2. Crea un bot y guarda el token.
3. Escribe a tu bot en un chat o grupo.
4. Obtiene el chat ID con un bot de ID o una llamada a la API de Telegram (segun tu metodo preferido).
5. Coloca `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`.

**Operacion diaria (SOP diario)**
1. Ejecuta Hunter en modo `--loop`.
2. Monitorea `runtime.log` cada 2-4 horas.
3. Revisa alertas en Telegram en menos de 10 minutos.
4. Prioriza posts con urgencia, precio claro y stack objetivo.
5. Responde rapido con una propuesta breve y concreta.
6. Registra el resultado (estado, precio, contacto).

**Operacion semanal (SOP semanal)**
1. Revisa `report.html` y los descartados.
2. Ajusta `TARGET_KEYWORDS` si hay ruido.
3. Revisa el score minimo en `config.py` (`MIN_SCORE_TO_ALERT`).
4. Limpia backlog y establece prioridades.

**Operacion mensual (SOP mensual)**
1. Respalda `opportunities.db`.
2. Revisa volumen de alertas vs. conversion.
3. Ajusta modelo de Ollama si hay errores de JSON.
4. Documenta cambios y resultados.

**Verificacion de salud (Checklist)**
1. `python main.py` no arroja errores.
2. `report.html` se actualiza tras cada ciclo.
3. `runtime.log` registra ciclo y contadores.
4. Telegram recibe alertas cuando el score supera el minimo.
5. `opportunities.db` contiene registros recientes.

**Procedimiento de respaldo**
1. Cierra procesos activos de Hunter.
2. Copia `opportunities.db` a una carpeta de backup con fecha.
3. Copia `runtime.log` si necesitas auditoria.

**Procedimiento de paro**
1. En la consola donde corre Hunter, presiona `Ctrl+C`.
2. Verifica que no quede otro proceso `python` ejecutando Hunter.

**Troubleshooting basico**
- Error: `Ollama error` o JSON invalido.
  Accion: cambia `OLLAMA_MODEL` o reinicia `ollama serve`.
- Error: `database is locked`.
  Accion: cierra procesos duplicados, espera 5-10 segundos y reintenta.
- No llegan alertas.
  Accion: verifica `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` y conexion.
- No hay resultados.
  Accion: aumenta `TARGET_KEYWORDS` o baja `MIN_SCORE_TO_ALERT`.

**Ajustes tacticos (cuando afinar)**
1. Keywords: `config.py` usa `TARGET_KEYWORDS` del `.env`.
2. Umbral de alerta: `config.py` -> `MIN_SCORE_TO_ALERT`.
3. Fuentes: `src\sources\reddit_source.py`.
4. Formato de reporte: `src\utils\report.py`.

**Disciplina operativa**
1. No compartas `.env` ni tokens.
2. No corras multiples instancias simultaneas.
3. Prioriza consistencia sobre cambios impulsivos.
4. Cada ajuste debe tener una razon y una medicion.

**Comandos rapidos**
```powershell
# Ejecutar un ciclo
python main.py

# Ejecutar en loop
python main.py --loop

# Ver ultimos logs
Get-Content runtime.log -Tail 50
```

**Notas finales**
Hunter es tu radar. Tu disciplina operacional convierte alertas en dinero. Mantente rapido, frio y consistente.
