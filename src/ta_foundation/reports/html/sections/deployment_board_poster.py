from __future__ import annotations

import re
from datetime import date
from html import escape
from typing import Any, Dict, List, Optional

from ta_foundation.reports.deployment_board import build_deployment_board_insight


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}%"


def _fmt_session_date(value: date) -> str:
    return f"{value.month}/{value.day}/{value:%y}"


def _title_case_board_phrase(value: str, *, suffix: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    normalized = re.sub(r"\s+", " ", text.replace("-", " "))
    pieces = [part.capitalize() for part in normalized.split(" ") if part]
    joined = " ".join(pieces)
    return f"{joined} {suffix}".strip()


def _extract_regime_label(parsed: Dict[str, Any]) -> str:
    line = str(parsed.get("current_regime_line") or "")
    match = re.search(r"that makes this a\s+(.+?)\s+board", line, flags=re.IGNORECASE)
    if match:
        return _title_case_board_phrase(match.group(1), suffix="Board").upper()
    return "BOARD ACTIVE"


def _extract_adds_only(parsed: Dict[str, Any]) -> str:
    line = str(parsed.get("current_regime_line") or "")
    match = re.search(
        r"adds are approved only on\s+(.+?)(?:\.\s*$|$)",
        line,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().upper()
    return "CONTROLLED CONFIRMATION"


def _market_label(rows: List[Dict[str, Any]], options: Dict[str, Any]) -> str:
    explicit = str(options.get("market") or "").strip()
    if explicit:
        return explicit.upper()
    for row in rows:
        run_id = str(row.get("run_id") or "")
        if "-" in run_id:
            return run_id.rsplit("-", 1)[-1].upper()
    return "NQ"


def _timeframe_label(options: Dict[str, Any]) -> str:
    explicit = str(options.get("timeframe_label") or "").strip()
    return explicit if explicit else "1-MINUTE"


def _headline_label(options: Dict[str, Any]) -> str:
    explicit = str(options.get("headline") or "").strip()
    return explicit if explicit else "ARES DEPLOYMENT CARD"


def _subtitle_label(options: Dict[str, Any]) -> str:
    explicit = str(options.get("subtitle") or "").strip()
    return (
        explicit
        if explicit
        else "POST-MEETING RECOMMENDED BOT DEPLOYMENT"
    )


def _tier_heading(tier: str) -> str:
    mapping = {
        "primary": "PRIMARY BOTS",
        "secondary": "SECONDARY BOTS",
        "reserve": "RESERVE BOTS",
    }
    return mapping.get(str(tier or "").lower(), str(tier or "-").upper())


def _window_label(row: Dict[str, Any]) -> str:
    label = str(((row.get("board_window") or {}).get("label")) or "").strip()
    return label if label else "-"


def _row_icon(row: Dict[str, Any]) -> str:
    session = str(row.get("session_label") or "").lower()
    if "london early" in session:
        return "X"
    if "london late" in session:
        return "L"
    if "pre-market" in session:
        return "C"
    if "power hour" in session:
        return "S"
    if "asia" in session:
        return "A"
    return "G"


def _section_table(rows: List[Dict[str, Any]], *, tier: str) -> str:
    if not rows:
        return (
            '<div class="tf-dbp-empty">'
            "<div>No matching bots in this tier.</div>"
            "</div>"
        )

    body_rows: List[str] = []
    for row in rows:
        body_rows.append(
            f"""
            <tr>
              <td class="tf-dbp-col-rank">{int(row.get("board_rank") or 0)}</td>
              <td class="tf-dbp-col-name">
                <div class="tf-dbp-name-wrap">
                  <span class="tf-dbp-name-icon">{escape(_row_icon(row))}</span>
                  <div>
                    <div class="tf-dbp-bot-name">{escape(str(row.get("run_id") or "-").upper())}</div>
                  </div>
                </div>
              </td>
              <td>{escape(_window_label(row))}</td>
              <td class="tf-dbp-col-odds tf-dbp-col-odds--trigger">{escape(_fmt_pct(row.get("trigger_odds")))}</td>
              <td class="tf-dbp-col-odds tf-dbp-col-odds--success">{escape(_fmt_pct(row.get("success_odds")))}</td>
              <td class="tf-dbp-col-rr">{escape(str(row.get("rr_text") or "-"))}</td>
              <td class="tf-dbp-col-reason">{escape(str(row.get("reason") or "-"))}</td>
            </tr>
            """
        )

    return f"""
    <section class="tf-dbp-tier tf-dbp-tier--{escape(tier)}">
      <div class="tf-dbp-tier-head">
        <div class="tf-dbp-tier-title">{escape(_tier_heading(tier))} ({len(rows)})</div>
      </div>
      <div class="tf-dbp-tier-body">
        <table class="tf-dbp-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Bot Name</th>
              <th>CO Time Window</th>
              <th>Trigger Odds</th>
              <th>Success Odds</th>
              <th>R/R</th>
              <th>Why It Fits</th>
            </tr>
          </thead>
          <tbody>
            {''.join(body_rows)}
          </tbody>
        </table>
      </div>
    </section>
    """


def render_deployment_board_poster(ctx: Dict[str, Any]) -> str:
    options: Dict[str, Any] = ctx.get("options") or {}
    packages = ctx.get("packages", {}) or {}

    board_text_path = str(options.get("board_text_path") or "").strip()
    if not board_text_path:
        return "<div><em>No deployment board text file was configured. Set options.board_text_path in the report YAML.</em></div>"

    as_of = None
    as_of_raw = str(options.get("as_of_date") or "").strip()
    if as_of_raw:
        try:
            as_of = date.fromisoformat(as_of_raw)
        except Exception:
            as_of = None

    try:
        board = build_deployment_board_insight(
            packages,
            board_text_path=board_text_path,
            as_of=as_of,
            strip_days=int(options.get("strip_days", 5)),
        )
    except FileNotFoundError:
        return f"<div><em>Deployment board file not found: {escape(board_text_path)}</em></div>"

    rows = board["rows"]
    parsed = board["parsed"]
    if not rows:
        return "<div><em>No deployment recommendations were found in the board text.</em></div>"

    grouped = {
        "primary": [row for row in rows if str(row.get("tier")) == "primary"],
        "secondary": [row for row in rows if str(row.get("tier")) == "secondary"],
        "reserve": [row for row in rows if str(row.get("tier")) == "reserve"],
    }

    account_posture = parsed.get("account_posture") or {}
    law_items = parsed.get("deployment_law") or []
    summary_text = str(parsed.get("summary_text") or "").strip() or "No summary text supplied."
    signer = str(parsed.get("signer") or "").strip()

    intro = " ".join(str(line).strip() for line in (parsed.get("intro_lines") or []) if str(line).strip())
    hero_note = intro if intro else "Structured from the deployment board memo."
    market = _market_label(rows, options)
    timeframe = _timeframe_label(options)
    headline = _headline_label(options)
    subtitle = _subtitle_label(options)
    regime_label = _extract_regime_label(parsed)
    adds_only = _extract_adds_only(parsed)

    law_html = "".join(f"<li>{escape(str(item))}</li>" for item in law_items)

    css = """
    <style>
      .tf-dbp {
        position: relative;
        overflow: hidden;
        border-radius: 24px;
        padding: 28px;
        color: #f7e7c1;
        background:
          radial-gradient(circle at 12% 20%, rgba(255,166,0,0.18), transparent 17%),
          radial-gradient(circle at 86% 12%, rgba(255,94,0,0.20), transparent 18%),
          radial-gradient(circle at 72% 28%, rgba(255,184,77,0.08), transparent 22%),
          linear-gradient(180deg, rgba(17,12,6,0.98), rgba(8,7,4,0.99));
        border: 3px solid rgba(190,126,22,0.78);
        box-shadow:
          0 28px 70px rgba(0,0,0,0.38),
          inset 0 0 0 1px rgba(255,214,128,0.24),
          inset 0 0 44px rgba(255,157,0,0.06);
        font-family: "Palatino Linotype", "Book Antiqua", Georgia, serif;
      }
      .tf-dbp::before,
      .tf-dbp::after {
        content: "";
        position: absolute;
        inset: 10px;
        border: 1px solid rgba(255,213,128,0.26);
        pointer-events: none;
      }
      .tf-dbp::after {
        inset: 18px;
        border-color: rgba(138,88,12,0.40);
      }
      .tf-dbp-headline {
        position: relative;
        text-align: center;
        padding: 8px 130px 0;
      }
      .tf-dbp-title {
        font-size: clamp(34px, 4.4vw, 64px);
        line-height: 0.95;
        letter-spacing: 0.08em;
        color: #f3bf52;
        text-shadow:
          0 1px 0 #5e3307,
          0 0 10px rgba(255,193,79,0.20),
          0 8px 26px rgba(0,0,0,0.42);
        font-weight: 700;
      }
      .tf-dbp-subtitle {
        margin-top: 8px;
        font-size: clamp(15px, 1.5vw, 24px);
        letter-spacing: 0.05em;
        color: #efe1c3;
        text-transform: uppercase;
        font-weight: 700;
      }
      .tf-dbp-crest,
      .tf-dbp-guard {
        position: absolute;
        top: 0;
        width: 92px;
        height: 92px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 38px;
        color: rgba(241,185,82,0.94);
        border: 2px solid rgba(196,132,23,0.82);
        background:
          radial-gradient(circle at 30% 25%, rgba(255,184,77,0.16), transparent 28%),
          linear-gradient(180deg, rgba(23,16,7,0.98), rgba(8,7,4,0.98));
        box-shadow:
          inset 0 0 0 1px rgba(255,215,153,0.16),
          0 14px 30px rgba(0,0,0,0.30);
      }
      .tf-dbp-crest {
        left: 0;
        clip-path: polygon(50% 0%, 82% 12%, 100% 42%, 92% 82%, 50% 100%, 8% 82%, 0% 42%, 18% 12%);
      }
      .tf-dbp-guard {
        right: 0;
        clip-path: polygon(50% 0%, 100% 20%, 86% 100%, 14% 100%, 0% 20%);
      }
      .tf-dbp-meta {
        margin-top: 24px;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0;
        border: 2px solid rgba(179,117,20,0.78);
        background: rgba(10,9,5,0.86);
        box-shadow: inset 0 0 0 1px rgba(255,214,138,0.12);
      }
      .tf-dbp-meta-cell {
        padding: 14px 18px;
        text-align: center;
        border-right: 1px solid rgba(179,117,20,0.78);
      }
      .tf-dbp-meta-cell:last-child { border-right: 0; }
      .tf-dbp-meta-k {
        font-size: 14px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #d6a446;
        font-weight: 700;
      }
      .tf-dbp-meta-v {
        margin-top: 5px;
        font-size: 18px;
        color: #fff0ce;
        font-weight: 700;
      }
      .tf-dbp-posture {
        margin-top: 18px;
        display: grid;
        grid-template-columns: 1.05fr 1.45fr 1fr 1.15fr;
        border: 2px solid rgba(179,117,20,0.78);
        background:
          radial-gradient(circle at 0% 50%, rgba(255,157,0,0.10), transparent 20%),
          rgba(9,8,4,0.88);
      }
      .tf-dbp-posture-cell {
        padding: 16px 18px;
        border-right: 1px solid rgba(179,117,20,0.74);
        min-height: 102px;
        display: flex;
        flex-direction: column;
        justify-content: center;
      }
      .tf-dbp-posture-cell:last-child { border-right: 0; }
      .tf-dbp-posture-label {
        font-size: 13px;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: #d6a446;
        font-weight: 700;
      }
      .tf-dbp-posture-value {
        margin-top: 8px;
        color: #fff0ce;
        font-size: 20px;
        font-weight: 700;
        line-height: 1.2;
      }
      .tf-dbp-posture-stack {
        display: grid;
        gap: 6px;
      }
      .tf-dbp-posture-line {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        align-items: baseline;
        font-size: 19px;
        color: #fff0ce;
      }
      .tf-dbp-posture-line span:first-child {
        color: #efe1c3;
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }
      .tf-dbp-posture-line strong {
        font-size: 22px;
      }
      .tf-dbp-posture-line--breakout strong { color: #96d629; }
      .tf-dbp-posture-line--regression strong { color: #ff5f3a; }
      .tf-dbp-posture-line--regime strong { color: #8ec63f; }
      .tf-dbp-note {
        margin-top: 10px;
        color: rgba(240,221,182,0.86);
        font-size: 13px;
        line-height: 1.45;
      }
      .tf-dbp-tiers {
        margin-top: 20px;
        display: grid;
        grid-template-columns: 1.34fr 1.18fr 1.18fr;
        gap: 14px;
        align-items: start;
      }
      .tf-dbp-tier {
        background: rgba(6,5,2,0.88);
        border: 2px solid rgba(179,117,20,0.80);
        box-shadow: inset 0 0 0 1px rgba(255,214,138,0.10);
        min-height: 100%;
      }
      .tf-dbp-tier-head {
        padding: 12px 16px;
        border-bottom: 1px solid rgba(179,117,20,0.74);
        background:
          linear-gradient(90deg, rgba(255,168,40,0.08), transparent 50%),
          rgba(13,11,5,0.92);
      }
      .tf-dbp-tier-title {
        font-size: 18px;
        color: #ecb84e;
        font-weight: 700;
        letter-spacing: 0.03em;
      }
      .tf-dbp-tier-body { padding: 0; }
      .tf-dbp-table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
      }
      .tf-dbp-table th,
      .tf-dbp-table td {
        border-right: 1px solid rgba(179,117,20,0.64);
        border-bottom: 1px solid rgba(179,117,20,0.56);
        padding: 10px 10px;
        vertical-align: top;
      }
      .tf-dbp-table th:last-child,
      .tf-dbp-table td:last-child { border-right: 0; }
      .tf-dbp-table th {
        color: #d6a446;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        font-size: 11px;
        font-weight: 700;
        background: rgba(14,12,6,0.94);
      }
      .tf-dbp-table td {
        color: #f4e7ca;
        font-size: 12px;
        line-height: 1.45;
      }
      .tf-dbp-col-rank {
        width: 44px;
        text-align: center;
        font-size: 20px;
        font-weight: 700;
        color: #f0c15c;
      }
      .tf-dbp-col-name { width: 28%; }
      .tf-dbp-name-wrap {
        display: grid;
        grid-template-columns: 34px minmax(0, 1fr);
        gap: 8px;
        align-items: start;
      }
      .tf-dbp-name-icon {
        width: 28px;
        height: 28px;
        border-radius: 999px;
        border: 1px solid rgba(233,176,71,0.74);
        display: inline-flex;
        align-items: center;
        justify-content: center;
        color: #f4c15b;
        font-size: 14px;
        font-weight: 700;
        background: radial-gradient(circle at 30% 30%, rgba(255,173,51,0.16), rgba(9,8,4,0.92));
      }
      .tf-dbp-bot-name {
        color: #f7ebd1;
        font-size: 14px;
        font-weight: 700;
        line-height: 1.2;
        overflow-wrap: anywhere;
      }
      .tf-dbp-col-odds {
        text-align: center;
        font-size: 17px;
        font-weight: 700;
      }
      .tf-dbp-col-odds--trigger { color: #98d52f; }
      .tf-dbp-col-odds--success { color: #f2c657; }
      .tf-dbp-col-rr {
        text-align: center;
        font-size: 16px;
        font-weight: 700;
        color: #f4e7ca;
      }
      .tf-dbp-col-reason {
        color: #efe1c3;
        font-size: 12px;
        line-height: 1.5;
      }
      .tf-dbp-empty {
        padding: 18px;
        color: rgba(240,221,182,0.84);
        font-size: 13px;
      }
      .tf-dbp-bottom {
        margin-top: 16px;
        display: grid;
        grid-template-columns: 1.25fr 1fr;
        gap: 14px;
      }
      .tf-dbp-panel {
        border: 2px solid rgba(179,117,20,0.80);
        background: rgba(7,6,3,0.90);
        box-shadow: inset 0 0 0 1px rgba(255,214,138,0.10);
      }
      .tf-dbp-panel-head {
        padding: 12px 16px;
        border-bottom: 1px solid rgba(179,117,20,0.74);
        color: #ecb84e;
        font-size: 18px;
        font-weight: 700;
        letter-spacing: 0.03em;
      }
      .tf-dbp-panel-body {
        padding: 14px 18px 16px;
        color: #efe1c3;
      }
      .tf-dbp-laws {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px 18px;
        margin: 0;
        padding-left: 20px;
      }
      .tf-dbp-laws li {
        margin: 0;
        line-height: 1.55;
      }
      .tf-dbp-summary {
        font-size: 14px;
        line-height: 1.65;
      }
      .tf-dbp-signer {
        margin-top: 12px;
        color: #d6a446;
        font-size: 13px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .tf-dbp-footer {
        margin-top: 16px;
        padding-top: 12px;
        border-top: 1px solid rgba(179,117,20,0.52);
        text-align: center;
        color: #f0c15c;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        font-size: 16px;
        font-weight: 700;
      }
      @media (max-width: 1280px) {
        .tf-dbp-headline { padding: 8px 88px 0; }
        .tf-dbp-crest, .tf-dbp-guard { width: 72px; height: 72px; font-size: 30px; }
        .tf-dbp-posture { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .tf-dbp-tiers { grid-template-columns: 1fr; }
        .tf-dbp-bottom { grid-template-columns: 1fr; }
      }
      @media (max-width: 820px) {
        .tf-dbp { padding: 18px; }
        .tf-dbp-headline { padding: 8px 0 0; }
        .tf-dbp-crest, .tf-dbp-guard { display: none; }
        .tf-dbp-meta { grid-template-columns: 1fr; }
        .tf-dbp-meta-cell { border-right: 0; border-bottom: 1px solid rgba(179,117,20,0.78); }
        .tf-dbp-meta-cell:last-child { border-bottom: 0; }
        .tf-dbp-posture { grid-template-columns: 1fr; }
        .tf-dbp-posture-cell { border-right: 0; border-bottom: 1px solid rgba(179,117,20,0.74); }
        .tf-dbp-posture-cell:last-child { border-bottom: 0; }
        .tf-dbp-laws { grid-template-columns: 1fr; }
      }
    </style>
    """

    return f"""
    {css}
    <section class="tf-dbp">
      <div class="tf-dbp-headline">
        <div class="tf-dbp-crest">⚔</div>
        <div class="tf-dbp-guard">Λ</div>
        <div class="tf-dbp-title">{escape(headline)}</div>
        <div class="tf-dbp-subtitle">{escape(subtitle)}</div>
        <div class="tf-dbp-note">{escape(hero_note)}</div>
      </div>

      <div class="tf-dbp-meta">
        <div class="tf-dbp-meta-cell">
          <div class="tf-dbp-meta-k">Session Date</div>
          <div class="tf-dbp-meta-v">{escape(_fmt_session_date(board["as_of"]))}</div>
        </div>
        <div class="tf-dbp-meta-cell">
          <div class="tf-dbp-meta-k">Market</div>
          <div class="tf-dbp-meta-v">{escape(market)}</div>
        </div>
        <div class="tf-dbp-meta-cell">
          <div class="tf-dbp-meta-k">Timeframe</div>
          <div class="tf-dbp-meta-v">{escape(timeframe)}</div>
        </div>
      </div>

      <div class="tf-dbp-posture">
        <div class="tf-dbp-posture-cell">
          <div class="tf-dbp-posture-label">Board Posture</div>
          <div class="tf-dbp-posture-stack">
            <div class="tf-dbp-posture-line tf-dbp-posture-line--breakout">
              <span>Breakout</span>
              <strong>{escape(_fmt_pct(parsed.get("breakout_pct")))}</strong>
            </div>
            <div class="tf-dbp-posture-line tf-dbp-posture-line--regression">
              <span>Regression</span>
              <strong>{escape(_fmt_pct(parsed.get("regression_pct")))}</strong>
            </div>
          </div>
        </div>
        <div class="tf-dbp-posture-cell">
          <div class="tf-dbp-posture-label">Regime</div>
          <div class="tf-dbp-posture-value" style="color:#8ec63f;">{escape(regime_label)}</div>
        </div>
        <div class="tf-dbp-posture-cell">
          <div class="tf-dbp-posture-label">Account Posture</div>
          <div class="tf-dbp-posture-value">
            {escape(str(account_posture.get("active") or "-"))} / {escape(str(account_posture.get("capacity") or "-"))} ACCOUNTS
          </div>
        </div>
        <div class="tf-dbp-posture-cell">
          <div class="tf-dbp-posture-label">Adds Only On</div>
          <div class="tf-dbp-posture-value" style="font-size:17px;">{escape(adds_only)}</div>
        </div>
      </div>

      <div class="tf-dbp-tiers">
        {_section_table(grouped["primary"], tier="primary")}
        {_section_table(grouped["secondary"], tier="secondary")}
        {_section_table(grouped["reserve"], tier="reserve")}
      </div>

      <div class="tf-dbp-bottom">
        <section class="tf-dbp-panel">
          <div class="tf-dbp-panel-head">Deployment Law</div>
          <div class="tf-dbp-panel-body">
            <ul class="tf-dbp-laws">{law_html}</ul>
          </div>
        </section>
        <section class="tf-dbp-panel">
          <div class="tf-dbp-panel-head">Summary</div>
          <div class="tf-dbp-panel-body tf-dbp-summary">
            <div>{escape(summary_text)}</div>
            {f'<div class="tf-dbp-signer">{escape(signer)}</div>' if signer else ''}
          </div>
        </section>
      </div>

      <div class="tf-dbp-footer">Discipline • Patience • Execution • Protect Capital • Let The Market Prove It</div>
    </section>
    """
