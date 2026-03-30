from __future__ import annotations

"""
Strategy Discovery — NinjaTrader Template Generator Section
============================================================
Reads Strategy Discovery analysis results and renders a downloadable
NinjaTrader 8 XML parameter template for the StrategyDiscoveryFilter strategy.

Options (from YAML):
  tick_value          : float  (default: 5.0 — NQ = $5/tick)
  tick_size           : float  (default: 0.25)
  show_reasoning      : bool   (default: true — show why each param was set)
  show_per_run        : bool   (default: true — one card per run)
  show_best_run_only  : bool   (default: false — if true, only best ranked run)
"""

import html
from typing import Any, Dict, List

from ta_foundation.analysis.strategy_discovery.nt_template_generator import (
    generate_nt_template,
    generate_per_rule_templates,
)
from ta_foundation.analysis.strategy_discovery.pantheon_bot_v2_template import (
    generate_pantheon_v2_template,
)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdnt-wrap { font-family: system-ui, sans-serif; margin: 0 0 32px; }
.sdnt-title { font-size: 22px; font-weight: 700; color: #1e293b; margin: 0 0 4px; }
.sdnt-subtitle { font-size: 13px; color: #64748b; margin: 0 0 24px; }

.sdnt-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  margin: 0 0 24px;
  overflow: hidden;
}
.sdnt-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  gap: 12px;
  flex-wrap: wrap;
}
.sdnt-run-id {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}
.sdnt-copy-btn {
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}
.sdnt-copy-btn:hover { background: #2563eb; }
.sdnt-copy-btn.copied { background: #10b981; }

.sdnt-warnings {
  margin: 12px 20px;
  background: #fffbeb;
  border: 1px solid #fcd34d;
  border-radius: 8px;
  padding: 10px 14px;
}
.sdnt-warnings ul { margin: 4px 0 0; padding-left: 18px; }
.sdnt-warnings li { font-size: 12px; color: #92400e; line-height: 1.6; }
.sdnt-warnings-label { font-size: 12px; font-weight: 700; color: #92400e; }

.sdnt-xml-wrap {
  margin: 12px 20px 16px;
  position: relative;
}
.sdnt-xml-code {
  display: block;
  background: #0f172a;
  color: #e2e8f0;
  font-family: "Cascadia Code", "Fira Mono", "Courier New", monospace;
  font-size: 11.5px;
  line-height: 1.55;
  padding: 16px 18px;
  border-radius: 8px;
  overflow-x: auto;
  white-space: pre;
  max-height: 420px;
  overflow-y: auto;
}

.sdnt-reasoning-wrap { padding: 0 20px 16px; }
.sdnt-reasoning-title {
  font-size: 12px;
  font-weight: 700;
  color: #475569;
  letter-spacing: .05em;
  text-transform: uppercase;
  margin: 0 0 8px;
}
.sdnt-reasoning-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.sdnt-reasoning-table th {
  text-align: left;
  padding: 6px 10px;
  background: #f1f5f9;
  color: #475569;
  font-weight: 600;
  border-bottom: 1px solid #e2e8f0;
}
.sdnt-reasoning-table td {
  padding: 6px 10px;
  border-bottom: 1px solid #f1f5f9;
  color: #334155;
  vertical-align: top;
}
.sdnt-reasoning-table tr:last-child td { border-bottom: none; }
.sdnt-param-key { font-weight: 600; color: #0f172a; white-space: nowrap; }
.sdnt-param-val { color: #2563eb; font-weight: 600; white-space: nowrap; }
.sdnt-param-reason { color: #64748b; }

.sdnt-no-data {
  padding: 32px 20px;
  text-align: center;
  color: #94a3b8;
  font-size: 14px;
}
.sdnt-instructions {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  padding: 14px 18px;
  margin: 0 0 20px;
  font-size: 13px;
  color: #14532d;
  line-height: 1.6;
}
.sdnt-instructions strong { color: #166534; }
</style>
"""

# ---------------------------------------------------------------------------
# Copy-to-clipboard JS
# ---------------------------------------------------------------------------

_COPY_JS = """
<script>
function sdntCopy(btnId, xmlId) {
  var btn = document.getElementById(btnId);
  var xml = document.getElementById(xmlId).textContent;
  navigator.clipboard.writeText(xml).then(function() {
    btn.textContent = '✓ Copied!';
    btn.classList.add('copied');
    setTimeout(function() {
      btn.textContent = 'Copy XML';
      btn.classList.remove('copied');
    }, 2500);
  }).catch(function() {
    // fallback: select text
    var el = document.getElementById(xmlId);
    var range = document.createRange();
    range.selectNode(el);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
  });
}
</script>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_param_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


# Which params to show in the reasoning table
# RegimeMode and ExitPolicy are now string enum values (e.g. "TrendingOnly", "FixedRR")
_REASONING_PARAMS = [
    ("RegimeMode", lambda p: str(p["RegimeMode"])),
    ("AdxThreshold", lambda p: str(p["AdxThreshold"])),
    ("StopTicks", lambda p: str(p["StopTicks"])),
    ("TargetTicks", lambda p: str(p["TargetTicks"])),
    ("ExitPolicy", lambda p: str(p["ExitPolicy"])),
    ("AtrTrailMultiple", lambda p: f"{p['AtrTrailMultiple']:.1f}"),
    ("AllowRTH/ONH/ETH", lambda p: f"RTH={str(p['AllowRTH']).lower()}, ONH={str(p['AllowONH']).lower()}, ETH={str(p['AllowETH']).lower()}"),
    ("AllowLong/Short", lambda p: f"Long={str(p['AllowLong']).lower()}, Short={str(p['AllowShort']).lower()}"),
    ("MaxDailyLossUsd/MaxDailyTrades", lambda p: f"${p['MaxDailyLossUsd']}, {p['MaxDailyTrades']} trades/day"),
]


def _render_run_card(run_id: str, sd: Dict[str, Any], options: Dict[str, Any], card_idx: int) -> str:
    """Render template card for a single run."""
    tick_value = float(options.get("tick_value") or 5.0)
    tick_size = float(options.get("tick_size") or 0.25)
    show_reasoning = bool(options.get("show_reasoning", True))

    template = generate_nt_template(
        sd,
        run_id=run_id,
        options={"tick_value": tick_value, "tick_size": tick_size},
    )

    xml_id = f"sdnt-xml-{card_idx}"
    btn_id = f"sdnt-btn-{card_idx}"
    xml_escaped = html.escape(template.xml_str)

    warnings_html = ""
    if template.warnings:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in template.warnings)
        warnings_html = (
            f'<div class="sdnt-warnings">'
            f'<span class="sdnt-warnings-label">Warnings</span>'
            f'<ul>{items}</ul>'
            f'</div>'
        )

    reasoning_html = ""
    if show_reasoning:
        rows = []
        for key, val_fn in _REASONING_PARAMS:
            reason = template.reasoning.get(key, "")
            try:
                val_str = val_fn(template.parameters)
            except Exception:
                val_str = "?"
            rows.append(
                f'<tr>'
                f'<td class="sdnt-param-key">{html.escape(key)}</td>'
                f'<td class="sdnt-param-val">{html.escape(val_str)}</td>'
                f'<td class="sdnt-param-reason">{html.escape(reason)}</td>'
                f'</tr>'
            )
        reasoning_html = (
            f'<div class="sdnt-reasoning-wrap">'
            f'<div class="sdnt-reasoning-title">Parameter Reasoning</div>'
            f'<table class="sdnt-reasoning-table">'
            f'<thead><tr>'
            f'<th>Parameter</th><th>Generated Value</th><th>Why</th>'
            f'</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            f'</table>'
            f'</div>'
        )

    return (
        f'<div class="sdnt-card">'
        f'<div class="sdnt-card-header">'
        f'<span class="sdnt-run-id">{html.escape(str(run_id))}</span>'
        f'<button id="{btn_id}" class="sdnt-copy-btn" '
        f'onclick="sdntCopy(\'{btn_id}\', \'{xml_id}\')">Copy XML</button>'
        f'</div>'
        f'{warnings_html}'
        f'<div class="sdnt-xml-wrap">'
        f'<pre class="sdnt-xml-code" id="{xml_id}">{xml_escaped}</pre>'
        f'</div>'
        f'{reasoning_html}'
        f'</div>'
    )


def _render_per_rule_cards(
    run_id: str,
    sd: Dict[str, Any],
    options: Dict[str, Any],
    card_start_idx: int,
) -> str:
    """Render per-signal-rule NT template cards for a __market_discovery__ package."""
    max_rules = int(options.get("max_per_rule_templates", 6))
    rule_templates = generate_per_rule_templates(
        sd, run_id=run_id, options=options, max_rules=max_rules
    )
    if not rule_templates:
        return ""

    show_reasoning = bool(options.get("show_reasoning", True))
    parts = [
        '<div style="margin-top:16px;font-size:.8rem;font-weight:700;'
        'text-transform:uppercase;letter-spacing:.05em;color:#475569;">'
        f'Per-Signal-Rule Templates ({len(rule_templates)} rules)'
        '</div>'
    ]

    for i, pr in enumerate(rule_templates):
        idx = card_start_idx + i
        xml_id = f"sdnt-xml-r-{idx}"
        btn_id = f"sdnt-btn-r-{idx}"
        xml_escaped = html.escape(pr.template.xml_str)

        # Header badge
        wr_str = f"{pr.best_win_rate * 100:.1f}%" if pr.best_win_rate is not None else "—"
        pf_str = f"{pr.best_pf:.2f}" if pr.best_pf is not None else "—"
        badges = (
            f'<span style="font-size:11px;background:#e0f2fe;color:#0369a1;'
            f'border-radius:4px;padding:2px 7px;margin-right:6px;font-weight:600;">'
            f'S:{pr.best_stop} / T:{pr.best_target} (RR:{pr.best_rr:.1f})'
            f'</span>'
            f'<span style="font-size:11px;background:#f0fdf4;color:#166534;'
            f'border-radius:4px;padding:2px 7px;margin-right:6px;font-weight:600;">'
            f'WR:{wr_str} PF:{pf_str}'
            f'</span>'
            f'<span style="font-size:11px;opacity:.55;">{pr.n_signals} signals</span>'
        )

        warnings_html = ""
        if pr.template.warnings:
            items = "".join(
                f'<li>{html.escape(w)}</li>' for w in pr.template.warnings
            )
            warnings_html = (
                f'<div class="sdnt-warnings">'
                f'<span class="sdnt-warnings-label">Warnings</span>'
                f'<ul>{items}</ul>'
                f'</div>'
            )

        reasoning_html = ""
        if show_reasoning and pr.template.parameters:
            rows = []
            for key, val_fn in _REASONING_PARAMS:
                reason = pr.template.reasoning.get(key, "")
                try:
                    val_str = val_fn(pr.template.parameters)
                except Exception:
                    val_str = "?"
                rows.append(
                    f'<tr>'
                    f'<td class="sdnt-param-key">{html.escape(key)}</td>'
                    f'<td class="sdnt-param-val">{html.escape(val_str)}</td>'
                    f'<td class="sdnt-param-reason">{html.escape(reason)}</td>'
                    f'</tr>'
                )
            reasoning_html = (
                f'<div class="sdnt-reasoning-wrap">'
                f'<div class="sdnt-reasoning-title">Parameter Reasoning</div>'
                f'<table class="sdnt-reasoning-table">'
                f'<thead><tr>'
                f'<th>Parameter</th><th>Generated Value</th><th>Why</th>'
                f'</tr></thead>'
                f'<tbody>{"".join(rows)}</tbody>'
                f'</table>'
                f'</div>'
            )

        rule_short = html.escape(pr.rule_str[:80])
        parts.append(
            f'<details style="margin:8px 0;">'
            f'<summary style="cursor:pointer;list-style:none;display:flex;'
            f'align-items:center;gap:8px;padding:10px 14px;'
            f'background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">'
            f'<span style="font-size:12px;font-weight:600;color:#0f172a;'
            f'min-width:52px;">Rule {pr.rule_rank}</span>'
            f'<span style="font-size:12px;color:#475569;flex:1">{rule_short}</span>'
            f'<span style="display:flex;gap:6px;flex-wrap:wrap">{badges}</span>'
            f'</summary>'
            f'<div style="border:1px solid #e2e8f0;border-top:none;border-radius:0 0 8px 8px;overflow:hidden">'
            f'<div style="display:flex;justify-content:flex-end;padding:8px 16px 0;">'
            f'<button id="{btn_id}" class="sdnt-copy-btn" '
            f'onclick="sdntCopy(\'{btn_id}\', \'{xml_id}\')">Copy XML</button>'
            f'</div>'
            f'{warnings_html}'
            f'<div class="sdnt-xml-wrap">'
            f'<pre class="sdnt-xml-code" id="{xml_id}">{xml_escaped}</pre>'
            f'</div>'
            f'{reasoning_html}'
            f'</div>'
            f'</details>'
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PantheonBotV2 card
# ---------------------------------------------------------------------------

_V2_REASONING_PARAMS = [
    ("MaxStop",           lambda p: f"{p['MaxStop']} ticks"),
    ("MaxTPRatio",        lambda p: f"{p['MaxTPRatio']:.2f}  (TP = {int(p['MaxStop'] * p['MaxTPRatio'])} ticks)"),
    ("Long / Short",      lambda p: f"Long={str(p['Long']).lower()}, Short={str(p['Short']).lower()}"),
    ("TrendRegimeFilter", lambda p: p["RequiredTrendRegimeFilter"]),
    ("VwapRegimeFilter",  lambda p: p["RequiredVwapRegimeFilter"]),
    ("BlockedVol",        lambda p: p["BlockedVolatilityRegimeFilter"]),
    ("LockIn",            lambda p: f"trigger={p['LockInTriggerTicks']}t, plus={p['LockInPlusTicks']}t"),
    ("Giveback",          lambda p: f"start={p['GivebackStartTicks']}t, trail={p['GivebackTicks']}t"),
    ("TimeFilter",        lambda p: f"{p['StartTimeH']:02d}:{p['StartTimeM']:02d} dur {p['DurationTimeH']}h{p['DurationTimeM']:02d}m"),
    ("LossStop / Profit", lambda p: f"${p['LossStop']:.0f} / ${p['ProfitStop']:.0f} daily"),
]


def _render_v2_run_card(
    run_id: str,
    sd: Dict[str, Any],
    options: Dict[str, Any],
    card_idx: int,
) -> str:
    """Render a PantheonBotV2 template card for a single run."""
    tick_value = float(options.get("tick_value") or 5.0)
    show_reasoning = bool(options.get("show_reasoning", True))

    try:
        template = generate_pantheon_v2_template(
            sd, run_id=run_id, options={"tick_value": tick_value}
        )
    except Exception as exc:
        return (
            f'<div class="sdnt-card" style="margin-top:8px">'
            f'<div class="sdnt-card-header">'
            f'<span class="sdnt-run-id">{html.escape(str(run_id))} — PantheonBotV2</span>'
            f'</div>'
            f'<div style="padding:12px;color:#ef4444">Error: {html.escape(str(exc))}</div>'
            f'</div>'
        )

    xml_id = f"sdnt-v2-xml-{card_idx}"
    btn_id = f"sdnt-v2-btn-{card_idx}"
    xml_escaped = html.escape(template.xml_str)

    warnings_html = ""
    if template.warnings:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in template.warnings)
        warnings_html = (
            f'<div class="sdnt-warnings">'
            f'<span class="sdnt-warnings-label">Warnings</span>'
            f'<ul>{items}</ul>'
            f'</div>'
        )

    reasoning_html = ""
    if show_reasoning:
        rows = []
        for key, val_fn in _V2_REASONING_PARAMS:
            reason = template.reasoning.get(key, "")
            try:
                val_str = val_fn(template.parameters)
            except Exception:
                val_str = "?"
            rows.append(
                f'<tr>'
                f'<td class="sdnt-param-key">{html.escape(key)}</td>'
                f'<td class="sdnt-param-val">{html.escape(val_str)}</td>'
                f'<td class="sdnt-param-reason">{html.escape(reason)}</td>'
                f'</tr>'
            )
        reasoning_html = (
            f'<div class="sdnt-reasoning-wrap">'
            f'<div class="sdnt-reasoning-title">PantheonBotV2 Parameter Reasoning</div>'
            f'<table class="sdnt-reasoning-table">'
            f'<thead><tr>'
            f'<th>Parameter</th><th>Generated Value</th><th>Why</th>'
            f'</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            f'</table>'
            f'</div>'
        )

    return (
        f'<div class="sdnt-card" style="margin-top:8px;border-color:#6366f1">'
        f'<div class="sdnt-card-header" style="background:#f5f3ff">'
        f'<span class="sdnt-run-id" style="color:#4f46e5">'
        f'{html.escape(str(run_id))} — PantheonBotV2'
        f'</span>'
        f'<button id="{btn_id}" class="sdnt-copy-btn" style="background:#6366f1" '
        f'onclick="sdntCopy(\'{btn_id}\', \'{xml_id}\')">Copy XML</button>'
        f'</div>'
        f'{warnings_html}'
        f'<div class="sdnt-xml-wrap">'
        f'<pre class="sdnt-xml-code" id="{xml_id}">{xml_escaped}</pre>'
        f'</div>'
        f'{reasoning_html}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_nt_template(ctx: dict) -> str:
    packages = ctx.get("packages") or {}
    options = ctx.get("options") or {}
    show_per_run = bool(options.get("show_per_run", True))
    show_best_run_only = bool(options.get("show_best_run_only", False))

    if not packages:
        return (
            f'{_CSS}'
            f'<div class="sdnt-wrap">'
            f'<div class="sdnt-title">NinjaTrader Template</div>'
            f'<div class="sdnt-no-data">No packages available.</div>'
            f'</div>'
        )

    # Collect runs that have strategy_discovery data
    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        if not sd:
            continue
        # Find ranking score if available
        ranking = sd.get("ranking") or {}
        score = None
        cross = ranking.get("cross_run") or []
        for entry in cross:
            if str(entry.get("run_id") or "") == str(run_id):
                score = entry.get("final_score")
                break
        run_items.append((run_id, sd, score))

    if not run_items:
        return (
            f'{_CSS}'
            f'<div class="sdnt-wrap">'
            f'<div class="sdnt-title">NinjaTrader Template</div>'
            f'<div class="sdnt-no-data">'
            f'No Strategy Discovery data found. '
            f'Enable <code>strategy_discovery: enabled: true</code> in your YAML.'
            f'</div>'
            f'</div>'
        )

    # Sort by score descending so best run is first
    run_items.sort(key=lambda x: (x[2] or 0), reverse=True)

    if show_best_run_only:
        run_items = run_items[:1]

    instructions_html = (
        f'<div class="sdnt-instructions">'
        f'<strong>How to use:</strong> '
        f'Copy the XML below → save as <code>MyTemplate.xml</code> → '
        f'paste into '
        f'<code>Documents\\NinjaTrader 8\\templates\\Strategy\\StrategyDiscoveryFilter\\</code> → '
        f'open StrategyDiscoveryFilter in NinjaTrader → click <em>Load Template</em>. '
        f'Run a backtest, then feed the results back into this report to refine further. '
        f'Parameters marked with warnings should be tuned manually before going live.'
        f'</div>'
    )

    cards_html = ""
    per_rule_count = 0  # tracks card index offset for unique IDs
    if show_per_run:
        for idx, (run_id, sd, _score) in enumerate(run_items):
            cards_html += _render_run_card(run_id, sd, options, idx)
            # PantheonBotV2 companion template
            cards_html += _render_v2_run_card(run_id, sd, options, idx)
            # For market_discovery packages: also render per-rule templates
            if str(run_id).startswith("__market_discovery__"):
                cards_html += _render_per_rule_cards(
                    run_id, sd, options,
                    card_start_idx=1000 + per_rule_count * 20,
                )
                per_rule_count += 1

    subtitle = (
        f"{len(run_items)} run{'s' if len(run_items) != 1 else ''} "
        f"{'(best ranked first)' if len(run_items) > 1 else ''}"
    )

    return (
        f'{_CSS}'
        f'{_COPY_JS}'
        f'<div class="sdnt-wrap">'
        f'<div class="sdnt-title">NinjaTrader Template Generator</div>'
        f'<div class="sdnt-subtitle">'
        f'Parameters auto-derived from Strategy Discovery analysis — {html.escape(subtitle)}'
        f'</div>'
        f'{instructions_html}'
        f'{cards_html}'
        f'</div>'
    )
