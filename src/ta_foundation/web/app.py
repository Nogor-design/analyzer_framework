from __future__ import annotations

"""
Strategy Composer Web UI
========================
Local Flask server that provides a browser interface for composing and
backtesting strategies using the Ollama LLM and the StrategyComposer pipeline.

Start with:
    python -m ta_foundation.web.app \\
        --market-data "C:/path/to/nt/exports" \\
        --db-path experiments.duckdb \\
        --port 7734

Then open: http://localhost:7734
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Lazy imports — Flask and pandas are not always available together
# ---------------------------------------------------------------------------
try:
    from flask import Flask, jsonify, request, render_template, Response
    _FLASK_OK = True
except ImportError:
    _FLASK_OK = False

import pandas as pd

# ---------------------------------------------------------------------------
# Global state (populated on startup)
# ---------------------------------------------------------------------------
_market_data_dir: Optional[str] = None
_db_path: Optional[str] = None
_bars_cache: Dict[str, pd.DataFrame] = {}
_schema_path = (
    Path(__file__).parent.parent
    / "strategies" / "TaFoundationExecutionBridge" / "templates"
    / "strategy_template_schema.json"
)

# ---------------------------------------------------------------------------
# Bar loading
# ---------------------------------------------------------------------------

def _load_bars(instrument: str, contract: str) -> pd.DataFrame:
    key = f"{instrument}_{contract}"
    if key in _bars_cache:
        return _bars_cache[key]

    if not _market_data_dir:
        raise RuntimeError("No --market-data directory configured.")

    from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
    from ta_foundation.core.registry import read_header_sample

    parser = MinuteBarsLastTxtParser()
    bars_parts = []
    for p in sorted(Path(_market_data_dir).rglob("*.txt")):
        try:
            header = read_header_sample(p)
            if not parser.can_parse(p, header):
                continue
            artifact = parser.parse(p, run_id=None)
            if artifact and artifact.data is not None:
                df = artifact.data
                # Check instrument match
                if instrument and instrument.upper() not in str(p).upper():
                    continue
                bars_parts.append(df)
        except Exception:
            continue

    if not bars_parts:
        raise RuntimeError(
            f"No 1m bar files found for {instrument}/{contract} in {_market_data_dir}"
        )

    bars = pd.concat(bars_parts, ignore_index=True)
    bars = bars.sort_values("dt").drop_duplicates(subset=["dt"]).reset_index(drop=True)
    _bars_cache[key] = bars
    return bars


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app() -> "Flask":
    if not _FLASK_OK:
        raise ImportError("Flask is required: pip install flask")

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
    )

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/schema")
    def get_schema():
        """Return the template schema for the UI editor."""
        try:
            with open(_schema_path, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/models")
    def get_models():
        """Return available Ollama models."""
        from ta_foundation.analysis.strategy_composer.llm import list_models
        models = list_models()
        return jsonify({"models": models})

    @app.route("/api/generate", methods=["POST"])
    def generate():
        """Stream LLM token generation to the client via SSE."""
        body = request.get_json(force=True)
        idea = body.get("idea", "").strip()
        model = body.get("model", "qwen3-coder:30b")

        if not idea:
            return jsonify({"error": "idea is required"}), 400

        from ta_foundation.analysis.strategy_composer.llm import OllamaComposer

        def _stream():
            composer = OllamaComposer(model=model)
            buffer = []

            try:
                for token in _token_stream(composer, idea):
                    buffer.append(token)
                    yield f"data: {json.dumps({'token': token})}\n\n"

                full_text = "".join(buffer)
                from ta_foundation.analysis.strategy_composer.llm import _extract_json
                template_dict = _extract_json(full_text)
                yield f"data: {json.dumps({'done': True, 'template': template_dict})}\n\n"

            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return Response(_stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/api/backtest", methods=["POST"])
    def backtest():
        """Run the full backtest pipeline on a template dict."""
        body = request.get_json(force=True)
        template_dict = body.get("template")
        instrument = body.get("instrument") or (template_dict or {}).get("instrument", "NQ")
        contract = body.get("contract") or (template_dict or {}).get("contract", "H25")

        if not template_dict:
            return jsonify({"error": "template is required"}), 400

        try:
            from ta_foundation.analysis.strategy_composer.template import StrategyTemplate
            from ta_foundation.analysis.strategy_composer.composer import StrategyComposer

            tmpl = StrategyTemplate.from_dict(template_dict)
            bars_1m = _load_bars(instrument, contract)

            composer = StrategyComposer(bars_1m=bars_1m, template=tmpl)
            result = composer.run()

            # Convert trades DataFrame to JSON-safe records
            trades_df = result.pop("trades", pd.DataFrame())
            result["trades"] = _trades_to_records(trades_df)
            result["n_trades"] = len(trades_df)

            return jsonify(result)

        except Exception as exc:
            traceback.print_exc()
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/validate", methods=["POST"])
    def validate():
        """Run validation gates on the backtest results."""
        body = request.get_json(force=True)
        template_dict = body.get("template")
        experiment_id = body.get("experiment_id")
        instrument = body.get("instrument") or (template_dict or {}).get("instrument", "NQ")
        contract = body.get("contract") or (template_dict or {}).get("contract", "H25")

        if not template_dict:
            return jsonify({"error": "template is required"}), 400

        try:
            from ta_foundation.analysis.strategy_composer.template import StrategyTemplate
            from ta_foundation.analysis.strategy_composer.composer import StrategyComposer

            tmpl = StrategyTemplate.from_dict(template_dict)
            bars_1m = _load_bars(instrument, contract)
            composer = StrategyComposer(bars_1m=bars_1m, template=tmpl)
            composer.run()

            vr = composer.validate(
                db_path=_db_path or None,
                experiment_id=int(experiment_id) if experiment_id else None,
            )

            return jsonify({
                "passed": vr.passed,
                "summary": vr.summary,
                "gates": [
                    {
                        "name": g.name,
                        "passed": g.passed,
                        "value": g.value,
                        "threshold": g.threshold,
                        "reason": g.reason,
                    }
                    for g in vr.gates
                ],
                "t_test": vr.t_test,
                "wf_results": {k: v for k, v in vr.wf_results.items() if k != "rolling"},
            })

        except Exception as exc:
            traceback.print_exc()
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/register", methods=["POST"])
    def register():
        """Register the hypothesis in the experiment registry."""
        body = request.get_json(force=True)
        template_dict = body.get("template", {})

        if not _db_path:
            return jsonify({"error": "No --db-path configured on the server."}), 400

        try:
            from ta_foundation.cli.register_hypothesis import register as _register

            exp_id = _register(
                _db_path,
                hypothesis=template_dict.get("hypothesis", ""),
                family=template_dict.get("entry_signal", {}).get("type"),
                signal_id=template_dict.get("entry_signal", {}).get("pattern") or
                          template_dict.get("template_name"),
                instrument=template_dict.get("instrument"),
                contract=template_dict.get("contract"),
                timeframe=template_dict.get("timeframe"),
            )
            return jsonify({"experiment_id": exp_id, "db_path": _db_path})

        except Exception as exc:
            traceback.print_exc()
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/experiments")
    def experiments():
        """List all registered experiments."""
        if not _db_path:
            return jsonify({"experiments": []})
        try:
            from ta_foundation.persistence.db import ExperimentRegistry
            reg = ExperimentRegistry(_db_path)
            return jsonify({"experiments": reg.list_experiments()})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_stream(composer, idea: str):
    """Yield tokens from the LLM stream."""
    import urllib.error
    buffer = []
    from ta_foundation.analysis.strategy_composer.llm import _post_stream, _SYSTEM_PROMPT
    import json as _json

    payload = {
        "model": composer.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": idea},
        ],
        "stream": True,
        "options": {"temperature": composer.temperature},
    }
    for token in _post_stream(f"{composer.base_url}/api/chat", payload):
        yield token


def _trades_to_records(trades: pd.DataFrame) -> list:
    if trades is None or trades.empty:
        return []
    out = trades.copy()
    # Convert timestamps to strings for JSON serialisation
    for col in out.select_dtypes(include=["datetimetz", "datetime64[ns, UTC]"]).columns:
        out[col] = out[col].astype(str)
    for col in out.select_dtypes(include=["datetime64[ns]"]).columns:
        out[col] = out[col].astype(str)
    return out.where(pd.notna(out), None).to_dict(orient="records")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m ta_foundation.web.app",
        description="Start the Strategy Composer web UI.",
    )
    ap.add_argument("--market-data", default=None,
                    help="Path to NinjaTrader export directory (minute bar files).")
    ap.add_argument("--db-path", default=None,
                    help="Path to DuckDB experiment registry file.")
    ap.add_argument("--port", type=int, default=7734,
                    help="Port to listen on (default: 7734).")
    ap.add_argument("--host", default="127.0.0.1",
                    help="Host to bind (default: 127.0.0.1).")
    args = ap.parse_args()

    if not _FLASK_OK:
        print("Flask is required: pip install flask", file=sys.stderr)
        sys.exit(1)

    global _market_data_dir, _db_path
    _market_data_dir = args.market_data
    _db_path = args.db_path

    print(f"[Composer] Market data: {_market_data_dir or '(none)'}")
    print(f"[Composer] DB path:     {_db_path or '(none)'}")
    print(f"[Composer] UI:          http://{args.host}:{args.port}")

    app = create_app()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
