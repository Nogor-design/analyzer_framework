"""
Market Data Dashboard — run with:  python market_data_dashboard.py
Then open http://localhost:5050
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError:
    sys.exit("Flask is required: pip install flask")

# ── file pattern regexes ─────────────────────────────────────────────────────
RE_MINUTE = re.compile(
    r"^(?P<instrument>[A-Z0-9]+)\s+(?P<contract>\d{2}-\d{2,4})\.Last\.txt$",
    re.IGNORECASE,
)
RE_TICK = re.compile(
    r"^(?P<instrument>[A-Z0-9]+)\s+(?P<contract>\d{2}-\d{2,4})\s+Tick\.Last(?:\s*\(\d+\))?\.txt$",
    re.IGNORECASE,
)
RE_DATE_SEMICOLON = re.compile(r"^(\d{8})[; ](\d{6})")
RE_DATE_YYYYMMDD   = re.compile(r"^(\d{8})")

STALE_DAYS = 30          # flag as stale if last bar older than this
MISSING_PARTNER = True   # warn when bars exist but ticks missing (or vice versa)


def _read_first_last_line(path: Path) -> tuple[str, str]:
    """Return (first_data_line, last_data_line) skipping header."""
    first = last = ""
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            low = line.lower()
            if any(k in low for k in ("date", "open", "time", "last", "bid", "ask")):
                if re.match(r"[a-zA-Z]", line):
                    continue
            if not first:
                first = line
            last = line
    return first, last


def _parse_dt(line: str) -> datetime | None:
    m = RE_DATE_SEMICOLON.match(line)
    if m:
        date_s, time_s = m.group(1), m.group(2)
    else:
        m2 = RE_DATE_YYYYMMDD.match(line)
        if not m2:
            return None
        date_s = m2.group(1)
        time_s = "000000"
    try:
        return datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _days_ago(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    now = datetime.now()
    return (now - dt).days


def _contract_sort_key(contract: str) -> tuple:
    """Turn 'MM-YY' or 'MM-YYYY' into a sortable tuple."""
    parts = contract.split("-")
    try:
        month = int(parts[0])
        year  = int(parts[1]) if len(parts) > 1 else 0
        if year < 100:
            year += 2000
        return (year, month)
    except (ValueError, IndexError):
        return (0, 0)


def scan_directory(folder: str) -> dict:
    base = Path(folder)
    if not base.exists():
        return {"error": f"Path does not exist: {folder}"}
    if not base.is_dir():
        return {"error": f"Not a directory: {folder}"}

    # Collect files
    minute_files: dict[tuple, Path] = {}
    tick_files:   dict[tuple, Path] = {}

    for f in base.iterdir():
        if not f.is_file():
            continue
        name = f.name

        m = RE_MINUTE.match(name)
        if m:
            key = (m.group("instrument").upper(), m.group("contract"))
            minute_files[key] = f
            continue

        t = RE_TICK.match(name)
        if t:
            key = (t.group("instrument").upper(), t.group("contract"))
            tick_files[key] = f

    all_keys = sorted(
        set(minute_files) | set(tick_files),
        key=lambda k: (_contract_sort_key(k[1]), k[0]),
    )

    rows = []
    for key in all_keys:
        instrument, contract = key
        has_bars  = key in minute_files
        has_ticks = key in tick_files

        def file_info(path: Path | None) -> dict:
            if path is None:
                return {"exists": False, "first_dt": None, "last_dt": None,
                        "days_stale": None, "size_mb": None, "filename": None}
            try:
                first_line, last_line = _read_first_last_line(path)
                first_dt = _parse_dt(first_line)
                last_dt  = _parse_dt(last_line)
            except Exception:
                first_dt = last_dt = None
            size_mb = round(path.stat().st_size / 1_048_576, 2)
            return {
                "exists":     True,
                "first_dt":   first_dt.isoformat() if first_dt else None,
                "last_dt":    last_dt.isoformat()  if last_dt  else None,
                "days_stale": _days_ago(last_dt),
                "size_mb":    size_mb,
                "filename":   path.name,
            }

        bars_info  = file_info(minute_files.get(key))
        ticks_info = file_info(tick_files.get(key))

        # Determine status
        issues = []
        for label, info in [("Bars", bars_info), ("Ticks", ticks_info)]:
            if info["exists"]:
                d = info["days_stale"]
                if d is not None and d > STALE_DAYS:
                    issues.append(f"{label} stale ({d}d old)")
        if has_bars and not has_ticks:
            issues.append("Tick file missing")
        if has_ticks and not has_bars:
            issues.append("Minute-bars file missing")

        status = "ok" if not issues else ("warning" if any("stale" in i for i in issues) else "missing")
        if has_bars and not has_ticks:
            status = "missing"

        rows.append({
            "instrument": instrument,
            "contract":   contract,
            "bars":       bars_info,
            "ticks":      ticks_info,
            "issues":     issues,
            "status":     status,
        })

    summary = {
        "total_pairs":   len(all_keys),
        "ok":            sum(1 for r in rows if r["status"] == "ok"),
        "warnings":      sum(1 for r in rows if r["status"] == "warning"),
        "missing":       sum(1 for r in rows if r["status"] == "missing"),
        "scanned_at":    datetime.now().isoformat(),
        "folder":        str(base.resolve()),
    }

    return {"rows": rows, "summary": summary}


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Market Data Dashboard</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --border: #2a2d3a;
    --text: #e2e8f0;
    --muted: #718096;
    --ok: #48bb78;
    --warn: #ed8936;
    --missing: #fc8181;
    --accent: #63b3ed;
    --input-bg: #252836;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 18px 24px; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; font-weight: 600; }
  header .subtitle { color: var(--muted); font-size: 12px; }
  .main { padding: 24px; max-width: 1400px; margin: 0 auto; }
  .scan-bar { display: flex; gap: 10px; margin-bottom: 24px; }
  .scan-bar input {
    flex: 1; background: var(--input-bg); border: 1px solid var(--border);
    color: var(--text); padding: 10px 14px; border-radius: 6px; font-size: 14px;
    outline: none;
  }
  .scan-bar input:focus { border-color: var(--accent); }
  .scan-bar button {
    background: var(--accent); color: #0f1117; border: none; padding: 10px 22px;
    border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 14px; white-space: nowrap;
  }
  .scan-bar button:hover { opacity: 0.88; }
  .scan-bar button:disabled { opacity: 0.45; cursor: not-allowed; }
  .summary-cards { display: flex; gap: 14px; margin-bottom: 24px; flex-wrap: wrap; }
  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 22px; min-width: 140px; flex: 1;
  }
  .card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .6px; margin-bottom: 6px; }
  .card .value { font-size: 28px; font-weight: 700; }
  .card.ok .value    { color: var(--ok); }
  .card.warn .value  { color: var(--warn); }
  .card.miss .value  { color: var(--missing); }
  .card.total .value { color: var(--accent); }
  .filter-bar { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }
  .filter-bar label { color: var(--muted); font-size: 12px; }
  .filter-bar select, .filter-bar input[type=text] {
    background: var(--input-bg); border: 1px solid var(--border); color: var(--text);
    padding: 6px 10px; border-radius: 5px; font-size: 13px;
  }
  table { width: 100%; border-collapse: collapse; }
  th { background: var(--surface); color: var(--muted); font-size: 11px; text-transform: uppercase;
       letter-spacing: .5px; text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border);
       position: sticky; top: 0; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  tr:hover td { background: rgba(99,179,237,.04); }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;
  }
  .badge.ok      { background: rgba(72,187,120,.15); color: var(--ok); }
  .badge.warning { background: rgba(237,137,54,.15);  color: var(--warn); }
  .badge.missing { background: rgba(252,129,129,.15); color: var(--missing); }
  .issues { color: var(--warn); font-size: 12px; }
  .issues.missing { color: var(--missing); }
  .muted { color: var(--muted); }
  .none-tag { color: var(--missing); font-size: 12px; }
  .ok-tag   { color: var(--ok);      font-size: 12px; }
  .stale-tag { color: var(--warn);   font-size: 12px; }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border);
             border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .empty { text-align: center; padding: 60px; color: var(--muted); }
  .error-msg { color: var(--missing); background: rgba(252,129,129,.1); border: 1px solid var(--missing);
               padding: 12px 16px; border-radius: 6px; margin-bottom: 20px; }
  .scanned-at { color: var(--muted); font-size: 11px; margin-bottom: 14px; }
  .table-wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }
  .col-instrument { width: 90px; font-weight: 600; }
  .col-contract   { width: 80px; }
  .col-status     { width: 90px; }
  .col-data       { width: 260px; }
  .col-issues     { }
  .file-name { color: var(--muted); font-size: 11px; margin-top: 2px; word-break: break-all; }
  .tag { display: inline-block; }
</style>
</head>
<body>
<header>
  <div>
    <h1>Market Data Dashboard</h1>
    <div class="subtitle">ta_foundation — NinjaTrader export scanner</div>
  </div>
</header>
<div class="main">
  <div class="scan-bar">
    <input id="pathInput" type="text" placeholder="Enter market data folder path…" />
    <button id="scanBtn" onclick="scan()">Scan</button>
  </div>
  <div id="errorBox" class="error-msg" style="display:none"></div>
  <div id="summaryCards" class="summary-cards" style="display:none">
    <div class="card total"><div class="label">Contracts</div><div class="value" id="cTotal">0</div></div>
    <div class="card ok">  <div class="label">Up to date</div><div class="value" id="cOk">0</div></div>
    <div class="card warn"><div class="label">Stale</div><div class="value" id="cWarn">0</div></div>
    <div class="card miss"><div class="label">Missing</div><div class="value" id="cMiss">0</div></div>
  </div>
  <div id="scannedAt" class="scanned-at"></div>
  <div id="filterBar" class="filter-bar" style="display:none">
    <label>Filter:</label>
    <input type="text" id="instrFilter" placeholder="Instrument" oninput="renderTable()" style="width:110px">
    <select id="statusFilter" onchange="renderTable()">
      <option value="">All statuses</option>
      <option value="ok">OK</option>
      <option value="warning">Stale</option>
      <option value="missing">Missing</option>
    </select>
  </div>
  <div class="table-wrap" id="tableWrap" style="display:none">
    <table>
      <thead>
        <tr>
          <th class="col-instrument">Instrument</th>
          <th class="col-contract">Contract</th>
          <th class="col-status">Status</th>
          <th class="col-data">Minute Bars</th>
          <th class="col-data">Tick Data</th>
          <th class="col-issues">Issues / Notes</th>
        </tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>
  <div id="emptyMsg" class="empty" style="display:none">No market data files found in this folder.</div>
</div>
<script>
let allRows = [];

async function scan() {
  const path = document.getElementById('pathInput').value.trim();
  if (!path) return;
  const btn = document.getElementById('scanBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  document.getElementById('errorBox').style.display = 'none';

  try {
    const res = await fetch('/api/scan?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (data.error) {
      showError(data.error);
      return;
    }
    allRows = data.rows;
    updateSummary(data.summary);
    renderTable();
    document.getElementById('scannedAt').textContent =
      'Scanned: ' + new Date(data.summary.scanned_at).toLocaleString() + ' — ' + data.summary.folder;
    document.getElementById('filterBar').style.display = 'flex';
  } catch(e) {
    showError('Request failed: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scan';
  }
}

function showError(msg) {
  const box = document.getElementById('errorBox');
  box.textContent = msg;
  box.style.display = 'block';
  document.getElementById('summaryCards').style.display = 'none';
  document.getElementById('tableWrap').style.display = 'none';
  document.getElementById('emptyMsg').style.display = 'none';
}

function updateSummary(s) {
  document.getElementById('cTotal').textContent = s.total_pairs;
  document.getElementById('cOk').textContent   = s.ok;
  document.getElementById('cWarn').textContent  = s.warnings;
  document.getElementById('cMiss').textContent  = s.missing;
  document.getElementById('summaryCards').style.display = 'flex';
}

function renderTable() {
  const instrQ  = document.getElementById('instrFilter').value.trim().toUpperCase();
  const statusQ = document.getElementById('statusFilter').value;

  const rows = allRows.filter(r => {
    if (instrQ  && !r.instrument.includes(instrQ)) return false;
    if (statusQ && r.status !== statusQ)           return false;
    return true;
  });

  const tbody = document.getElementById('tableBody');
  if (rows.length === 0) {
    tbody.innerHTML = '';
    document.getElementById('tableWrap').style.display = 'none';
    document.getElementById('emptyMsg').style.display = 'block';
    return;
  }
  document.getElementById('tableWrap').style.display = 'block';
  document.getElementById('emptyMsg').style.display = 'none';

  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="col-instrument">${r.instrument}</td>
      <td class="col-contract">${r.contract}</td>
      <td><span class="badge ${r.status}">${r.status === 'ok' ? 'OK' : r.status === 'warning' ? 'Stale' : 'Missing'}</span></td>
      <td>${fileCell(r.bars)}</td>
      <td>${fileCell(r.ticks)}</td>
      <td>${issueCell(r.issues)}</td>
    </tr>
  `).join('');
}

function fileCell(info) {
  if (!info.exists) return '<span class="none-tag">— not found</span>';
  const first = info.first_dt ? fmtDt(info.first_dt) : '?';
  const last  = info.last_dt  ? fmtDt(info.last_dt)  : '?';
  const age   = info.days_stale;
  const ageTag = age === null ? '' :
    age > 30 ? `<span class="stale-tag">(${age}d ago)</span>` :
               `<span class="ok-tag">(${age}d ago)</span>`;
  return `
    <div>${first} → ${last} ${ageTag}</div>
    <div class="file-name muted">${info.filename} &nbsp; ${info.size_mb} MB</div>
  `;
}

function issueCell(issues) {
  if (!issues || issues.length === 0) return '<span class="muted">—</span>';
  const cls = issues.some(i => i.toLowerCase().includes('missing')) ? 'missing' : '';
  return issues.map(i => `<div class="issues ${cls}">${i}</div>`).join('');
}

function fmtDt(iso) {
  if (!iso) return '?';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {year:'numeric',month:'short',day:'numeric'});
}

// Allow Enter key to trigger scan
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('pathInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') scan();
  });
  // Restore last path from localStorage
  const saved = localStorage.getItem('mdd_last_path');
  if (saved) document.getElementById('pathInput').value = saved;
});

// Save path on scan
const origScan = scan;
scan = async function() {
  const p = document.getElementById('pathInput').value.trim();
  if (p) localStorage.setItem('mdd_last_path', p);
  await origScan();
};
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/scan")
def api_scan():
    folder = request.args.get("path", "").strip()
    if not folder:
        return jsonify({"error": "No path provided"}), 400
    result = scan_directory(folder)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("MDD_PORT", 5050))
    print(f"\n  Market Data Dashboard → http://localhost:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False)
