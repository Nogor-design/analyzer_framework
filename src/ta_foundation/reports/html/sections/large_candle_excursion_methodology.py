from __future__ import annotations

"""
Large Candle Excursion — Methodology Section
=============================================
Renders a plain-language explanation of how the analysis works.
Reads the effective config from the data dict and includes it in the explanation.
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_data(ctx: dict) -> Optional[Dict]:
    if ctx.get("large_candle_excursion"):
        return ctx["large_candle_excursion"]
    for pkg in (ctx.get("packages") or {}).values():
        derived = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in derived:
            return derived["large_candle_excursion"]
    return None


def _section_title(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:28px;margin-bottom:12px">{text}</h3>'
    )


def _sub(text: str) -> str:
    return f'<h4 style="color:#2c3e50;margin-top:18px;margin-bottom:8px">{text}</h4>'


def _p(text: str) -> str:
    return f'<p style="color:#444;font-size:13px;line-height:1.7;margin-bottom:10px">{text}</p>'


def _li(text: str) -> str:
    return f'<li style="color:#444;font-size:13px;line-height:1.7;margin-bottom:4px">{text}</li>'


def _code(text: str) -> str:
    return f'<code style="background:#f4f4f4;padding:2px 5px;border-radius:3px;font-size:12px">{text}</code>'


def _cfg_row(label: str, value: Any) -> str:
    return (
        f'<tr>'
        f'<td style="padding:5px 12px;border:1px solid #ddd;font-weight:600;font-size:12px;background:#f9f9f9">{label}</td>'
        f'<td style="padding:5px 12px;border:1px solid #ddd;font-size:12px">{value}</td>'
        f'</tr>'
    )


def _render_config_table(cfg: Dict) -> str:
    candle_cfg = cfg.get("candle_size", {})
    fwd_cfg    = cfg.get("forward_window", {})
    tick_cfg   = cfg.get("tick_analysis", {})
    rev_cfg    = tick_cfg.get("reversal", {})

    rows: List[str] = [
        _cfg_row("Timeframes",        str(cfg.get("timeframes", [1, 5]))),
        _cfg_row("Direction Filter",  str(cfg.get("direction", "both"))),
        _cfg_row("Min Events",        str(cfg.get("min_events", 20))),
        _cfg_row("Average Lookbacks", str(candle_cfg.get("average_lookbacks", [10, 20]))),
        _cfg_row("Average Basis",     str(candle_cfg.get("average_basis", {}))),
        _cfg_row("Threshold Mode",    str(candle_cfg.get("threshold_mode", "multiplier"))),
        _cfg_row("Threshold Values",  str(
            candle_cfg.get("threshold_multipliers", []) or
            candle_cfg.get("threshold_percents", [])
        )),
        _cfg_row("Min Body Ticks",    str(candle_cfg.get("min_body_ticks", 2))),
        _cfg_row("Min Range Ticks",   str(candle_cfg.get("min_range_ticks", 4))),
        _cfg_row("Tick Size",         str(candle_cfg.get("tick_size", 0.25))),
        _cfg_row("Forward Windows",   str(fwd_cfg.get("minutes", [5, 10, 15, 30]))),
        _cfg_row("Use Tick Data",     str(tick_cfg.get("use_tick_data", False))),
    ]
    if tick_cfg.get("use_tick_data", False):
        rows += [
            _cfg_row("Reversal Ticks", str(rev_cfg.get("reversal_ticks", 8))),
            _cfg_row("Min Swing Ticks", str(rev_cfg.get("min_swing_ticks", 2))),
        ]

    return (
        '<table style="border-collapse:collapse;margin-bottom:16px">'
        '<thead><tr>'
        '<th style="background:#2c3e50;color:#fff;padding:7px 12px;border:1px solid #ddd;font-size:12px">Parameter</th>'
        '<th style="background:#2c3e50;color:#fff;padding:7px 12px;border:1px solid #ddd;font-size:12px">Value</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
    )


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_methodology(ctx: dict) -> str:
    data = _get_data(ctx)
    cfg  = (data or {}).get("config", {})

    html = '<div style="font-family:Arial,sans-serif">'
    html += _section_title("Methodology — Large Candle Forward Excursion")

    html += _sub("What this report measures")
    html += _p(
        "This report identifies every candle whose size exceeds a configurable threshold "
        "relative to a rolling average, then measures how price behaves in the N minutes after "
        "that candle closes. It is <strong>not a trade-entry report</strong> — it measures raw "
        "price path characteristics to inform stop/target design and strategy development."
    )

    html += _sub("Qualifying candle detection")
    html += '<ul style="margin:8px 0 12px 20px">'
    html += _li(
        "<strong>Body size (body basis):</strong> "
        + _code("body = abs(close − open)")
        + ". A rolling mean of body over the prior N bars is computed (lag-safe: current bar excluded). "
        "The ratio " + _code("body / rolling_mean(body, shift=1)") + " is compared to the threshold."
    )
    html += _li(
        "<strong>Full range (range basis):</strong> "
        + _code("total_range = high − low")
        + ". Same rolling comparison on the full candle range."
    )
    html += _li(
        "A candle qualifies when its ratio ≥ threshold (multiplier mode) or ≥ threshold/100 (percent mode). "
        "Min/max tick guards are applied after the ratio test."
    )
    html += _li(
        "<strong>Direction:</strong> bullish = close ≥ open (doji treated as bullish). "
        "Bearish = close < open."
    )
    html += '</ul>'

    html += _sub("Rolling average — look-ahead bias")
    html += _p(
        "All rolling averages are computed with "
        + _code("shift(1)")
        + " — the current candle is <strong>never included</strong> in its own baseline. "
        "This mirrors the production trading condition where you see the completed candle and compare it "
        "to what preceded it."
    )

    html += _sub("Forward analysis window")
    html += _p(
        "The forward window is <strong>time-based, not bar-count-based</strong>. "
        "This ensures comparability across timeframes (e.g., a 5m bar's 15-minute forward window "
        "always covers the same clock duration whether analysis runs on 1m or 5m bars)."
    )
    html += '<ul style="margin:8px 0 12px 20px">'
    html += _li(
        "<strong>Signal close time</strong> = bar open timestamp + timeframe duration. "
        "For a 5m bar opening at 09:30, close time = 09:35."
    )
    html += _li(
        "<strong>Forward window</strong> = (close_time, close_time + window_minutes]. "
        "Open on the left (signal bar excluded), closed on the right."
    )
    html += _li(
        "Only 1-minute bars within this time range are scanned — "
        "after-hours bars are naturally excluded if a session filter was applied."
    )
    html += '</ul>'

    html += _sub("Excursion definitions")
    html += '<ul style="margin:8px 0 12px 20px">'
    html += _li(
        "<strong>max_up_pts</strong> = max(bar highs in window) − signal close price"
    )
    html += _li(
        "<strong>max_down_pts</strong> = signal close price − min(bar lows in window)"
    )
    html += _li(
        "<strong>Favorable excursion</strong> = max_up_pts for bullish candles; "
        "max_down_pts for bearish candles."
    )
    html += _li(
        "<strong>Adverse excursion</strong> = max_down_pts for bullish candles; "
        "max_up_pts for bearish candles."
    )
    html += _li(
        "Excursion is converted to ticks by dividing by tick_size."
    )
    html += '</ul>'

    html += _sub("Time to max")
    html += _p(
        _code("time_to_max_fav_min")
        + " = minutes from signal close time to the bar that set the maximum favorable extreme. "
        "Reported as bar end time (bar open time + 1 minute) for 1m bar data. "
        "Useful for understanding how quickly continuation occurs after a large candle."
    )

    html += _sub("Fav Dominant % and Fav First %")
    html += '<ul style="margin:8px 0 12px 20px">'
    html += _li(
        "<strong>Fav Dominant %</strong>: percentage of events where "
        + _code("fav_ticks > adv_ticks")
        + " — i.e., price moved further in the expected direction than against it."
    )
    html += _li(
        "<strong>Fav First %</strong>: percentage of events where the maximum favorable "
        "extreme was reached <em>before</em> the maximum adverse extreme (by bar timing)."
    )
    html += '</ul>'

    if cfg.get("tick_analysis", {}).get("use_tick_data", False):
        rev_cfg = cfg.get("tick_analysis", {}).get("reversal", {})
        rev_ticks = rev_cfg.get("reversal_ticks", 8)
        html += _sub("Tick-path reversal analysis")
        html += _p(
            "When tick data is enabled, each event's forward window is scanned tick by tick. "
            f"A reversal is defined as: price retracing "
            + _code(f"{rev_ticks} ticks ({rev_ticks * float(cfg.get('candle_size', {}).get('tick_size', 0.25))} points)")
            + " from its running peak (for bullish events) or trough (for bearish events)."
        )
        html += '<ul style="margin:8px 0 12px 20px">'
        html += _li(
            _code("tick_pre_reversal_fav_ticks")
            + ": maximum favorable move (in ticks) reached before the first reversal."
        )
        html += _li(
            _code("tick_pre_reversal_adv_ticks")
            + ": maximum adverse move (in ticks) tracked before the first favorable reversal."
        )
        html += _li(
            "If no reversal occurs within the window, the full window extreme is reported."
        )
        html += '</ul>'

    if cfg:
        html += _sub("Effective Configuration")
        html += _render_config_table(cfg)

    html += '</div>'
    return html
