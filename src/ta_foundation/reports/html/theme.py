from __future__ import annotations

def default_css() -> str:
    return """
    :root {
      --bg: #0b0f14;
      --card: #101824;
      --text: #e6edf3;
      --muted: #9fb0c0;
      --border: #1f2a3a;
      --accent: #6aa6ff;
      --danger: #ff6a6a;
      --ok: #6aff9a;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji";
    }

    html, body { background: var(--bg); color: var(--text); font-family: var(--sans); margin: 0; }
    a { color: var(--accent); text-decoration: none; }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 24px; }
    .header { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin-bottom: 18px; }
    .title { font-size: 22px; font-weight: 650; }
    .subtitle { color: var(--muted); font-size: 13px; }

    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 12px; }
    .card {
      grid-column: span 12;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px 14px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.20);
    }
    .card h2 { margin: 0 0 10px 0; font-size: 16px; color: var(--text); }
    .muted { color: var(--muted); }
    .mono { font-family: var(--mono); }

    .kpis { display: grid; grid-template-columns: repeat(12, 1fr); gap: 10px; }
    .kpi { grid-column: span 3; background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 12px; padding: 10px; }
    .kpi .label { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    .kpi .value { font-size: 16px; font-weight: 650; }
    .kpi .sub { color: var(--muted); font-size: 11px; margin-top: 4px; }

    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--border); padding: 8px 6px; text-align: left; }
    th { color: var(--muted); font-weight: 600; }
    .right { text-align: right; }
    .pill { display: inline-block; padding: 2px 8px; border: 1px solid var(--border); border-radius: 999px; font-size: 12px; color: var(--muted); }
    .row { display:flex; gap:10px; flex-wrap:wrap; }

    img { max-width: 100%; border-radius: 10px; border: 1px solid var(--border); }
    """
