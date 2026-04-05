from __future__ import annotations

def default_css() -> str:
    return """
    /* ── Design tokens ─────────────────────────────────────────── */
    :root {
      --bg:       #f1f3f6;
      --card:     #ffffff;
      --text:     #1a1f2e;
      --muted:    #64748b;
      --border:   #e2e6ec;
      --accent:   #2563eb;
      --danger:   #dc2626;
      --ok:       #16a34a;
      --shadow:   0 1px 3px rgba(0,0,0,.06), 0 4px 16px rgba(0,0,0,.06);
      --radius:   12px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
              "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
              Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
    }

    /* ── Reset / base ───────────────────────────────────────────── */
    *, *::before, *::after { box-sizing: border-box; }
    html, body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.55;
      margin: 0;
      -webkit-font-smoothing: antialiased;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* ── Layout ─────────────────────────────────────────────────── */
    .wrap { max-width: 1400px; margin: 0 auto; padding: 28px 24px 48px; }

    /* ── Page header ────────────────────────────────────────────── */
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 24px;
      padding-bottom: 18px;
      border-bottom: 2px solid var(--border);
    }
    .title {
      font-size: 24px;
      font-weight: 700;
      color: var(--text);
      letter-spacing: -0.3px;
    }
    .subtitle { color: var(--muted); font-size: 13px; margin-top: 3px; }

    /* ── Section grid ───────────────────────────────────────────── */
    .grid { display: flex; flex-direction: column; gap: 20px; }

    /* ── Cards ──────────────────────────────────────────────────── */
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 22px 24px 24px;
      box-shadow: var(--shadow);
    }
    .card h2 {
      margin: 0 0 16px 0;
      font-size: 17px;
      font-weight: 700;
      color: var(--text);
      letter-spacing: -0.2px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
    }

    /* ── Section sub-headings (produced by section renderers) ────── */
    .card h3 {
      font-size: 14px;
      font-weight: 700;
      color: var(--text);
      margin: 20px 0 8px;
    }

    /* ── Utility ────────────────────────────────────────────────── */
    .muted   { color: var(--muted); }
    .mono    { font-family: var(--mono); font-size: 12px; }
    .right   { text-align: right !important; }
    .row     { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }

    /* ── Pills / badges ─────────────────────────────────────────── */
    .pill {
      display: inline-block;
      padding: 3px 10px;
      background: #f0f4ff;
      border: 1px solid #c7d7f8;
      border-radius: 999px;
      font-size: 12px;
      color: #3b5bdb;
      font-weight: 500;
    }

    /* ── KPI grid ───────────────────────────────────────────────── */
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 4px;
    }
    .kpi {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
    }
    .kpi .label  { color: var(--muted); font-size: 11px; font-weight: 600;
                   text-transform: uppercase; letter-spacing: .4px; margin-bottom: 5px; }
    .kpi .value  { font-size: 20px; font-weight: 700; color: var(--text); }
    .kpi .sub    { color: var(--muted); font-size: 11px; margin-top: 3px; }

    /* ── Tables ─────────────────────────────────────────────────── */
    /* Wrapper added by sections that want horizontal scroll */
    .tbl-wrap    { overflow-x: auto; -webkit-overflow-scrolling: touch; }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    thead th {
      background: #1e2a3a;
      color: #ffffff;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .5px;
      padding: 9px 10px;
      border: 1px solid #2d3f55;
      white-space: nowrap;
    }
    tbody tr:nth-child(even) td { background: #f8fafc; }
    tbody tr:hover td { background: #eef3ff !important; transition: background .1s; }
    td {
      padding: 7px 10px;
      border-bottom: 1px solid var(--border);
      border-left: 1px solid var(--border);
      font-size: 13px;
      vertical-align: middle;
    }
    td:first-child { border-left: none; }

    /* Legacy th/td (section renderers that build their own tables) */
    th { color: var(--muted); font-weight: 600; }

    /* ── Inline-style table overrides ───────────────────────────── */
    /* Sections emit inline style="background:#fff" on td — we normalise
       hover + striping via !important on the row rule above, but keep
       cells light so colored PF cells still read correctly.           */

    /* ── Images ─────────────────────────────────────────────────── */
    img {
      max-width: 100%;
      border-radius: 8px;
      border: 1px solid var(--border);
      display: block;
    }

    /* ── Scrollbar (Webkit) ─────────────────────────────────────── */
    ::-webkit-scrollbar       { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #f1f1f1; }
    ::-webkit-scrollbar-thumb { background: #c1c9d4; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #9aa5b4; }

    /* ── Responsive ─────────────────────────────────────────────── */
    @media (max-width: 768px) {
      .wrap  { padding: 16px; }
      .card  { padding: 16px; }
      .title { font-size: 20px; }
      .kpis  { grid-template-columns: repeat(2, 1fr); }
    }
    """
