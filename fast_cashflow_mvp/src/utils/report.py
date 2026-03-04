from datetime import datetime
import csv

def _escape(s: str) -> str:
    if not isinstance(s, str):
        return str(s)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
    )

def write_csv_report(path: str, items: list[dict]):
    """Export items to CSV format for external analysis."""
    if not items:
        return
        
    # Ensure all items have the same keys for CSV consistency
    all_keys = set()
    for item in items:
        all_keys.update(item.keys())
    
    keys = sorted(list(all_keys))
    with open(path, 'w', newline='', encoding='utf-8') as f:
        dict_writer = csv.DictWriter(f, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(items)

def write_report(path: str, items: list[dict], meta: dict):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    alerts = [i for i in items if i.get("status") == "alert"]
    trash = [i for i in items if i.get("status") != "alert"]

    def render_item(i: dict) -> str:
        stack = i.get("tech_stack")
        if isinstance(stack, list):
            stack_str = ", ".join(stack)
        else:
            stack_str = str(stack or "")
            
        hint = i.get("playbook_hint", "")
        confidence = i.get("match_confidence", 0)
        
        hint_html = f"<div class='hint'>💡 <b>Playbook:</b> {_escape(hint)}</div>" if hint else ""
        confidence_html = f"<div class='confidence'>🎯 Confidence: {confidence}%</div>" if confidence else ""

        return f"""
        <div class="card">
          <div class="row">
            <div>
              <div class="title">{_escape(i.get('title', 'Sin titulo'))}</div>
              <div class="muted">{_escape(i.get('summary', ''))}</div>
            </div>
            <div class="score">{i.get('total_score', 0)}</div>
          </div>
          {confidence_html}
          <div class="meta">
            <span>Precio: {_escape(i.get('price_str', 'N/A'))}</span>
            <span>Stack: {_escape(stack_str)}</span>
          </div>
          <div class="meta">
            <span>Reason: {_escape(i.get('reason', 'ok'))}</span>
            <span>Source: {_escape(i.get('source', 'reddit'))}</span>
            <span>Author: {_escape(i.get('author', 'unknown'))}</span>
          </div>
          {hint_html}
          <a class="link" href="{_escape(i.get('url', '#'))}">Abrir post</a>
        </div>
        """

    search_links = "".join(
        f"<li><a href=\"{_escape(u)}\">{_escape(u)}</a></li>" for u in meta.get("sources", [])
    )

    alerts_html = "".join(render_item(i) for i in alerts)
    trash_html = "".join(render_item(i) for i in trash)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fast-Cashflow Engine - Reporte Operativo</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Space+Grotesk:wght@400;500;600&display=swap");
    :root {{
      --bg: #0f141b;
      --bg-2: #151c26;
      --card: #1a2330;
      --accent: #ffc857;
      --accent-2: #4bd1a0;
      --ink: #e9eef5;
      --muted: #a9b4c2;
      --line: rgba(255, 255, 255, 0.08);
      --hint-bg: rgba(255, 200, 87, 0.1);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: radial-gradient(1200px 600px at 10% -10%, #233048 0%, transparent 60%),
                  radial-gradient(900px 500px at 90% 10%, #1f3a32 0%, transparent 60%),
                  var(--bg);
      font-family: "Space Grotesk", "Segoe UI", sans-serif;
      line-height: 1.6;
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 48px 24px 80px;
    }}
    header {{
      border: 1px solid var(--line);
      padding: 28px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0));
      box-shadow: 0 20px 60px rgba(0,0,0,0.35);
    }}
    h1 {{
      margin: 0 0 10px;
      font-family: "Fraunces", serif;
      font-size: 34px;
    }}
    h2 {{
      margin: 36px 0 12px;
      font-family: "Fraunces", serif;
      font-size: 24px;
    }}
    .meta {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-top: 18px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      height: 100%;
    }}
    .row {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }}
    .title {{
      font-weight: 600;
      font-size: 16px;
    }}
    .score {{
      font-weight: 700;
      color: var(--accent-2);
      font-size: 20px;
    }}
    .confidence {{
      font-size: 12px;
      color: var(--accent-2);
      margin-bottom: 8px;
    }}
    .hint {{
      background: var(--hint-bg);
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      margin: 12px 0;
      border-left: 3px solid var(--accent);
    }}
    .link {{
      color: var(--accent);
      display: inline-block;
      margin-top: auto;
      text-decoration: none;
      font-weight: 600;
    }}
    details {{
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
      background: rgba(255, 255, 255, 0.03);
    }}
    summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--muted);
    }}
    ul {{
      margin: 8px 0 0 18px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Fast-Cashflow Engine - Reporte Operativo</h1>
      <div class="meta">
        <span>Generado: {now}</span>
        <span>Fuentes consultadas: {len(meta.get('sources', []))}</span>
        <span>Items analizados: {len(items)}</span>
        <span>Alertas: {len(alerts)}</span>
      </div>
    </header>

    <h2>Alertas (alta prioridad)</h2>
    <div class="grid">
      {alerts_html}
    </div>

    <h2>Basura (descartado)</h2>
    <details>
      <summary>Ver descartados y razones</summary>
      <div class="grid">
        {trash_html}
      </div>
    </details>

    <h2>Fuentes y Busquedas</h2>
    <div class="card" style="margin-top: 24px;">
      <div class="meta">El sistema consulto estas URLs en el ciclo:</div>
      <ul>
        {search_links}
      </ul>
    </div>
  </div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
