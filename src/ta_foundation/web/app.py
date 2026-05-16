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
    from flask import Flask, jsonify, request, render_template, Response, send_file
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
_job_manager = None
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
    global _job_manager
    if _job_manager is None:
        from ta_foundation.web.jobs import JobManager

        _job_manager = JobManager(cwd=Path.cwd())

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

    @app.route("/api/capabilities")
    def get_capabilities():
        """Return runnable workflow metadata for the UI capability picker."""
        from ta_foundation.web.capabilities import (
            list_capabilities,
            list_capability_groups,
            list_report_section_categories,
            list_report_sections,
        )

        return jsonify(
            {
                "capabilities": list_capabilities(),
                "capability_groups": list_capability_groups(),
                "report_sections": list_report_sections(),
                "report_section_categories": list_report_section_categories(),
            }
        )

    @app.route("/discovery")
    def discovery_page():
        """Render the Discovery UI page.

        Resolves a session through the long-lived `ta_discovery_session_id`
        cookie. When the cookie is missing or points at a deleted session, a
        fresh session is created and the cookie reset. The session_id is
        injected into the template so the JS bootstrap can hit
        /api/discovery/sessions/<sid> immediately on load without a
        round-trip to figure out who it is.
        """
        from ta_foundation.web.discovery_session import (
            create_session,
            get_session,
        )

        cookie_name = "ta_discovery_session_id"
        cookie_sid = (request.cookies.get(cookie_name) or "").strip()
        session = get_session(cookie_sid) if cookie_sid else None
        set_new_cookie = False
        if session is None:
            session = create_session(label="", instrument_symbol="NQ")
            set_new_cookie = True

        response = app.make_response(
            render_template(
                "discovery.html",
                session_id=session.id,
                lce_only=False,
            )
        )
        if set_new_cookie:
            # 30 days; httpOnly so JS can't snoop, samesite Lax for nav OK.
            response.set_cookie(
                cookie_name,
                session.id,
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                samesite="Lax",
            )
        return response

    @app.route("/discovery/lce")
    def discovery_lce_page():
        """Render the Discovery UI in Large Candle Excursion-only mode.

        Reuses the same template and JS as /discovery, but the JS bootstrap
        hides the funnel stepper and pre-selects the LCE event-study stage.
        Same cookie-based session resolution.
        """
        from ta_foundation.web.discovery_session import (
            create_session,
            get_session,
        )

        cookie_name = "ta_discovery_session_id"
        cookie_sid = (request.cookies.get(cookie_name) or "").strip()
        session = get_session(cookie_sid) if cookie_sid else None
        set_new_cookie = False
        if session is None:
            session = create_session(label="", instrument_symbol="NQ")
            set_new_cookie = True

        response = app.make_response(
            render_template(
                "discovery.html",
                session_id=session.id,
                lce_only=True,
            )
        )
        if set_new_cookie:
            response.set_cookie(
                cookie_name,
                session.id,
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                samesite="Lax",
            )
        return response

    @app.route("/discovery/sessions")
    def discovery_sessions_page():
        """Render the sessions index page (rename / resume / delete)."""
        from ta_foundation.web.discovery_session import list_sessions

        cookie_name = "ta_discovery_session_id"
        active_sid = (request.cookies.get(cookie_name) or "").strip()
        return render_template(
            "discovery_sessions.html",
            sessions=list_sessions(),
            active_session_id=active_sid,
        )

    @app.route("/discovery/sessions/<session_id>/resume")
    def discovery_session_resume(session_id: str):
        """Set the discovery session cookie and redirect to /discovery.

        GET-only because it's invoked from the sessions index page as a
        plain link. The local web app has no auth model, so CSRF is not a
        concern here. Unknown ids fall through to /discovery, which will
        recreate or reuse a session as it normally does.
        """
        from flask import redirect
        from ta_foundation.web.discovery_session import get_session

        target = "/discovery"
        session = get_session(session_id)
        response = app.make_response(redirect(target, code=302))
        if session is not None:
            response.set_cookie(
                "ta_discovery_session_id",
                session.id,
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                samesite="Lax",
            )
        return response

    @app.route("/api/discovery/stages")
    def discovery_stages():
        """Return funnel stages (1-6) and event studies (LCE) for the stepper."""
        from ta_foundation.web.discovery_stages import list_funnel_stages, list_event_studies, list_families

        return jsonify(
            {
                "funnel_stages": list_funnel_stages(),
                "event_studies": list_event_studies(),
                "families": list_families(),
            }
        )

    @app.route("/api/discovery/stages/<stage_id>")
    def discovery_stage(stage_id: str):
        """Return one stage with its full default_yaml so the configure form can hydrate."""
        from ta_foundation.web.discovery_stages import get_stage

        stage = get_stage(stage_id, include_default_yaml=True)
        if stage is None:
            return jsonify({"error": f"Unknown stage id: {stage_id}"}), 404
        return jsonify({"stage": stage})

    @app.route("/api/discovery/stages/<stage_id>/preview", methods=["POST"])
    def discovery_stage_preview(stage_id: str):
        """Build the stage YAML from form values, validate, and return a preview.

        Body fields (all optional unless marked):
            instrument_symbol      str   (default: "NQ")
            overrides              dict  free-form deep-merge overrides
            disabled_families      list[str]
            report_title           str
            output_filename        str
            input_folder           str   required for validation pass
            output_folder          str   required for validation pass
            market_data_folder     str   required for validation pass
            report_config_path     str   used in command preview
            recursive              bool  (default: True)
            no_tick_data           bool  (default: True)
        """
        body = request.get_json(force=True) or {}
        from ta_foundation.web.discovery_builder import build_stage_payload

        try:
            payload = build_stage_payload(
                stage_id,
                instrument_symbol=str(body.get("instrument_symbol") or "NQ"),
                overrides=body.get("overrides") if isinstance(body.get("overrides"), dict) else None,
                disabled_families=body.get("disabled_families") or None,
                report_title=body.get("report_title") or None,
                output_filename=body.get("output_filename") or None,
                input_folder=body.get("input_folder"),
                output_folder=body.get("output_folder"),
                market_data_folder=body.get("market_data_folder"),
                recursive=bool(body.get("recursive", True)),
                no_tick_data=bool(body.get("no_tick_data", True)),
                report_config_path=body.get("report_config_path"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(
            {
                "stage_id": payload.stage_id,
                "report_yaml": payload.report_yaml,
                "command_preview": payload.command_preview,
                "validation": payload.validation,
            }
        ), (200 if payload.validation["ok"] else 400)

    @app.route("/api/discovery/glossary")
    def discovery_glossary():
        """Return the Discovery glossary for tooltip / panel rendering."""
        from ta_foundation.web.discovery_glossary import get_glossary

        try:
            payload = get_glossary().to_dict()
        except Exception as exc:  # noqa: BLE001 — surface YAML errors to the UI
            return jsonify({"error": f"Failed to load glossary: {exc}"}), 500
        return jsonify(payload)

    @app.route("/api/discovery/instruments")
    def discovery_instruments():
        """Return the instrument registry for the Discovery UI's instrument picker.

        Each entry includes tick_size, tick_value, point_value, RTH session
        defaults, and a short note. The Discovery UI uses these values directly
        when generating stage YAML — they are the source of truth for the UI.
        """
        from ta_foundation.web.discovery_instruments import (
            default_instrument,
            list_instruments,
        )

        return jsonify(
            {
                "default_symbol": default_instrument().symbol,
                "instruments": list_instruments(),
            }
        )

    # ------------------------------------------------------------------
    # Discovery session + run + promotion routes (Step 7)
    # ------------------------------------------------------------------

    @app.route("/api/discovery/sessions", methods=["GET"])
    def discovery_sessions_list():
        from ta_foundation.web.discovery_session import list_sessions

        return jsonify({"sessions": list_sessions()})

    @app.route("/api/discovery/sessions", methods=["POST"])
    def discovery_sessions_create():
        body = request.get_json(silent=True) or {}
        from ta_foundation.web.discovery_session import (
            ProjectContext,
            create_session,
        )

        try:
            session = create_session(
                label=str(body.get("label") or ""),
                instrument_symbol=str(body.get("instrument_symbol") or "NQ"),
                context=ProjectContext.from_dict(body.get("context") or {}),
                current_stage=str(body.get("current_stage") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"Failed to create session: {exc}"}), 400
        return jsonify({"session": session.load_document().to_dict()}), 201

    @app.route("/api/discovery/sessions/<session_id>", methods=["GET"])
    def discovery_session_get(session_id: str):
        from ta_foundation.web.discovery_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        doc = session.load_document().to_dict()
        runs = _runs_with_live_status(session)
        promotions = [p.to_dict() for p in session.list_promotions()]
        return jsonify({"session": doc, "runs": runs, "promotions": promotions})

    @app.route("/api/discovery/sessions/<session_id>", methods=["PATCH"])
    def discovery_session_patch(session_id: str):
        from ta_foundation.web.discovery_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        body = request.get_json(silent=True) or {}
        if "label" in body:
            session.update_label(str(body["label"] or ""))
        if "instrument_symbol" in body and body["instrument_symbol"]:
            from ta_foundation.web.discovery_instruments import get_instrument

            symbol = str(body["instrument_symbol"]).strip()
            if get_instrument(symbol) is None:
                return jsonify({"error": f"Unknown instrument: {symbol}"}), 400
            session.set_instrument(symbol)
        if "current_stage" in body:
            session.set_current_stage(str(body["current_stage"] or ""))
        if isinstance(body.get("context"), dict):
            session.update_context(**body["context"])
        return jsonify({"session": session.load_document().to_dict()})

    @app.route("/api/discovery/sessions/<session_id>", methods=["DELETE"])
    def discovery_session_delete(session_id: str):
        from ta_foundation.web.discovery_session import delete_session

        deleted = delete_session(session_id)
        if not deleted:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        return jsonify({"deleted": True, "session_id": session_id})

    @app.route(
        "/api/discovery/sessions/<session_id>/stages/<stage_id>/form",
        methods=["PUT"],
    )
    def discovery_session_set_form(session_id: str, stage_id: str):
        from ta_foundation.web.discovery_session import get_session
        from ta_foundation.web.discovery_stages import get_stage_definition

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        if get_stage_definition(stage_id) is None:
            return jsonify({"error": f"Unknown stage id: {stage_id}"}), 404
        body = request.get_json(silent=True) or {}
        values = body.get("values") if isinstance(body.get("values"), dict) else body
        session.set_form_values(stage_id, dict(values or {}))
        return jsonify({"session": session.load_document().to_dict()})

    @app.route("/api/discovery/sessions/<session_id>/runs", methods=["GET"])
    def discovery_session_runs_list(session_id: str):
        from ta_foundation.web.discovery_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        return jsonify({"runs": _runs_with_live_status(session)})

    @app.route("/api/discovery/sessions/<session_id>/runs", methods=["POST"])
    def discovery_session_run_dispatch(session_id: str):
        """Build the stage YAML, save it under the session, and dispatch the CLI.

        Body fields (all optional unless marked):
            stage_id           str   required
            instrument_symbol  str   defaults to session instrument
            overrides          dict  free-form deep-merge overrides
            disabled_families  list[str]
            report_title       str
            output_filename    str
            input_folder       str   required for the CLI to do anything
            output_folder      str   required
            market_data_folder str   required
            recursive          bool  default True
            no_tick_data       bool  default True
        """
        from ta_foundation.web.discovery_builder import (
            build_stage_payload,
        )
        from ta_foundation.web.discovery_session import (
            StageRun,
            _now_iso,
            get_session,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        body = request.get_json(silent=True) or {}
        stage_id = str(body.get("stage_id") or "").strip()
        if not stage_id:
            return jsonify({"error": "stage_id is required"}), 400

        doc = session.load_document()
        instrument_symbol = str(
            body.get("instrument_symbol") or doc.instrument.symbol or "NQ"
        )
        ctx = doc.context

        try:
            payload = build_stage_payload(
                stage_id,
                instrument_symbol=instrument_symbol,
                overrides=body.get("overrides") if isinstance(body.get("overrides"), dict) else None,
                disabled_families=body.get("disabled_families") or None,
                report_title=body.get("report_title") or None,
                output_filename=body.get("output_filename") or None,
                input_folder=body.get("input_folder") or ctx.input_folder or None,
                output_folder=body.get("output_folder") or ctx.output_folder or None,
                market_data_folder=body.get("market_data_folder") or ctx.market_data_folder or None,
                recursive=bool(body.get("recursive", ctx.recursive)),
                no_tick_data=bool(body.get("no_tick_data", ctx.no_tick_data)),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if not payload.validation["ok"]:
            return jsonify({"validation": payload.validation}), 400

        # Persist the generated YAML inside the session directory so the run is
        # reproducible later, even after the user closes the tab.
        yaml_path = session.write_stage_yaml(stage_id, payload.report_yaml)

        # Build the actual CLI command (resolved paths, not the preview shape)
        command = _build_discovery_cli_command(
            input_folder=body.get("input_folder") or ctx.input_folder,
            output_folder=body.get("output_folder") or ctx.output_folder,
            market_data_folder=body.get("market_data_folder") or ctx.market_data_folder,
            report_config_path=str(yaml_path),
            recursive=bool(body.get("recursive", ctx.recursive)),
            no_tick_data=bool(body.get("no_tick_data", ctx.no_tick_data)),
        )

        job = _job_manager.start(kind=f"discovery.{stage_id}", command=command)

        # Compute the eventual sidecar path. Output filename comes from the
        # generated YAML's report.output_filename. The sidecar name is
        # derived from the HTML stem so each stage gets its own JSON
        # (previously every stage shared `discovery_summary.json` and later
        # runs silently overwrote earlier ones).
        from ta_foundation.web.discovery_summary import sidecar_path_for_report

        output_filename = (
            (payload.config.get("report") or {}).get("output_filename")
            or f"{stage_id}.html"
        )
        out_dir = body.get("output_folder") or ctx.output_folder or ""
        report_html_path = str(Path(out_dir) / output_filename) if out_dir else ""
        summary_json_path = (
            str(sidecar_path_for_report(report_html_path)) if report_html_path else ""
        )

        run = StageRun(
            stage_id=stage_id,
            job_id=job.id,
            yaml_path=str(yaml_path),
            report_html_path=report_html_path,
            summary_json_path=summary_json_path,
            started_at=_now_iso(),
            status=job.status,
        )
        session.append_run(run)

        return jsonify({"job": job.as_dict(), "run": run.to_dict()}), 202

    @app.route(
        "/api/discovery/sessions/<session_id>/runs/<job_id>/summary",
        methods=["GET"],
    )
    def discovery_session_run_summary(session_id: str, job_id: str):
        """Return the discovery summary sidecar produced by a finished run.

        Reads the per-stage sidecar (e.g. `01_quick_scan_summary.json`).
        Falls back to the legacy `discovery_summary.json` filename so runs
        rendered before the per-stage rename still surface in the UI.
        """
        from ta_foundation.web.discovery_session import get_session
        from ta_foundation.web.discovery_summary import (
            read_summary,
            resolve_sidecar_path,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        run = next((r for r in session.list_runs() if r.job_id == job_id), None)
        if run is None:
            return jsonify({"error": f"Unknown job for this session: {job_id}"}), 404

        candidate = Path(run.summary_json_path) if run.summary_json_path else None
        sidecar_path: Path | None = None
        if candidate and candidate.exists():
            sidecar_path = candidate
        elif run.report_html_path:
            sidecar_path = resolve_sidecar_path(run.report_html_path)

        if sidecar_path is None:
            return jsonify({"error": "Sidecar not yet written.", "run": run.to_dict()}), 404
        try:
            data = read_summary(sidecar_path)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"Failed to read sidecar: {exc}"}), 500
        return jsonify({"summary": data, "run": run.to_dict()})

    @app.route("/api/discovery/sessions/<session_id>/promotions", methods=["GET"])
    def discovery_session_promotions_list(session_id: str):
        from ta_foundation.web.discovery_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        return jsonify(
            {"promotions": [p.to_dict() for p in session.list_promotions()]}
        )

    @app.route("/api/discovery/sessions/<session_id>/promotions", methods=["POST"])
    def discovery_session_promotions_append(session_id: str):
        from ta_foundation.web.discovery_session import (
            Promotion,
            _now_iso,
            get_session,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": f"Unknown session: {session_id}"}), 404
        body = request.get_json(silent=True) or {}
        from_stage = str(body.get("from_stage") or "").strip()
        to_stage = str(body.get("to_stage") or "").strip()
        if not from_stage or not to_stage:
            return jsonify({"error": "from_stage and to_stage are required"}), 400
        try:
            rank = int(body.get("rank") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "rank must be an integer"}), 400
        overrides = body.get("yaml_overrides") if isinstance(body.get("yaml_overrides"), dict) else {}
        promotion = Promotion(
            from_stage=from_stage,
            to_stage=to_stage,
            rank=rank,
            promoted_at=_now_iso(),
            yaml_overrides=overrides,
            explain=str(body.get("explain") or ""),
        )
        session.append_promotion(promotion)
        return jsonify({"promotion": promotion.to_dict()}), 201

    @app.route("/api/report-catalog")
    def report_catalog():
        """Return report templates plus CLI parameter metadata for report generation."""
        from ta_foundation.web.report_catalog import list_cli_parameters, list_report_templates

        return jsonify(
            {
                "cli_parameters": list_cli_parameters(),
                "report_templates": list_report_templates(),
            }
        )

    @app.route("/api/report-builder/from-template", methods=["POST"])
    def report_builder_from_template():
        """Generate report YAML from a curated template and user-edited YAML values."""
        body = request.get_json(force=True)
        from ta_foundation.web.report_catalog import build_template_yaml
        from ta_foundation.web.report_builder import build_command_preview, validate_report_request

        try:
            report_yaml = build_template_yaml(
                str(body.get("template_id") or ""),
                body.get("values") if isinstance(body.get("values"), dict) else {},
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        validation = validate_report_request(body, report_yaml)
        return jsonify(
            {
                "report_yaml": report_yaml,
                "command_preview": build_command_preview(body),
                "validation": validation,
            }
        ), (200 if validation["ok"] else 400)

    @app.route("/api/report-builder/preview", methods=["POST"])
    def report_builder_preview():
        """Generate report YAML and validation feedback without running the report job."""
        body = request.get_json(force=True)
        from ta_foundation.web.report_builder import build_report_builder_payload

        result = build_report_builder_payload(body)
        return jsonify(
            {
                "report_yaml": result.report_yaml,
                "command_preview": result.command_preview,
                "validation": result.validation,
            }
        )

    @app.route("/api/report-builder/validate", methods=["POST"])
    def report_builder_validate():
        """Validate generated or edited report YAML without running ingest or rendering."""
        body = request.get_json(force=True)
        from ta_foundation.web.report_builder import validate_report_request

        validation = validate_report_request(body)
        return jsonify({"validation": validation}), (200 if validation["ok"] else 400)

    @app.route("/api/report-builder/save", methods=["POST"])
    def report_builder_save():
        """Validate generated report YAML and save it to the requested path."""
        body = request.get_json(force=True)
        from ta_foundation.web.report_builder import save_report_yaml

        saved = save_report_yaml(body)
        return jsonify(
            {
                "path": saved.path,
                "validation": saved.validation,
            }
        ), (200 if saved.validation["ok"] else 400)

    @app.route("/api/report-builder/load", methods=["POST"])
    def report_builder_load():
        """Load and validate a reusable report YAML file from the requested path."""
        body = request.get_json(force=True)
        from ta_foundation.web.report_builder import load_report_yaml

        loaded = load_report_yaml(body)
        return jsonify(
            {
                "path": loaded.path,
                "report_yaml": loaded.report_yaml,
                "command_preview": loaded.command_preview,
                "validation": loaded.validation,
            }
        ), (200 if loaded.report_yaml else 400)

    @app.route("/api/report-builder/run", methods=["POST"])
    def report_builder_run():
        """Run the existing CLI report job with a saved report YAML file."""
        body = request.get_json(force=True)
        from ta_foundation.web.report_builder import (
            build_report_command_args,
            save_report_yaml,
            validate_report_run_request,
        )

        if str(body.get("report_yaml") or "").strip():
            saved = save_report_yaml(body)
            if not saved.validation["ok"]:
                return jsonify({"validation": saved.validation}), 400
            body = {**body, "report_config_path": saved.path}

        validation = validate_report_run_request(body)
        if not validation["ok"]:
            return jsonify({"validation": validation}), 400
        command = build_report_command_args(body)
        job = _job_manager.start(kind="report", command=command)
        return jsonify({"job": job.as_dict(), "validation": validation})

    @app.route("/api/prediction/run", methods=["POST"])
    def prediction_run():
        """Run an existing prediction CLI entry point as a background job."""
        body = request.get_json(force=True)
        from ta_foundation.web.prediction_jobs import (
            build_prediction_command_args,
            validate_prediction_job_request,
        )

        validation = validate_prediction_job_request(body)
        if not validation["ok"]:
            return jsonify({"validation": validation}), 400
        command = build_prediction_command_args(body)
        job = _job_manager.start(kind="prediction", command=command)
        return jsonify({"job": job.as_dict(), "validation": validation})

    @app.route("/api/jobs")
    def jobs_list():
        """List recent local web jobs."""
        return jsonify({"jobs": [job.as_dict() for job in _job_manager.list()]})

    @app.route("/api/jobs/<job_id>")
    def job_status(job_id: str):
        """Return status and captured output for one local web job."""
        job = _job_manager.get(job_id)
        if job is None:
            return jsonify({"error": f"Unknown job id: {job_id}"}), 404
        return jsonify({"job": job.as_dict()})

    @app.route("/api/jobs/<job_id>/log")
    def job_log(job_id: str):
        """Return job stdout from byte-offset `since` (default 0).

        Used by the discovery run-watch panel to tail output without re-fetching
        the full record on each poll.
        """
        job = _job_manager.get(job_id)
        if job is None:
            return jsonify({"error": f"Unknown job id: {job_id}"}), 404
        try:
            since = int(request.args.get("since") or 0)
        except (TypeError, ValueError):
            since = 0
        full = job.output or ""
        if since < 0 or since > len(full):
            since = len(full)
        return jsonify(
            {
                "output_since": full[since:],
                "length": len(full),
                "status": job.status,
                "returncode": job.returncode,
                "error": job.error,
            }
        )

    @app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    def job_cancel(job_id: str):
        """Request cancellation of a running job."""
        job = _job_manager.get(job_id)
        if job is None:
            return jsonify({"error": f"Unknown job id: {job_id}"}), 404
        accepted = bool(_job_manager.cancel(job_id))
        return jsonify({"accepted": accepted, "job": _job_manager.get(job_id).as_dict()})

    @app.route("/api/artifact")
    def artifact():
        """Serve a generated local artifact, such as a report HTML file."""
        raw_path = str(request.args.get("path") or "").strip()
        if not raw_path:
            return jsonify({"error": "path is required"}), 400
        path = Path(raw_path)
        if _job_manager is None or not _job_manager.is_allowed_artifact(path):
            return jsonify({"error": "Artifact is not available from a web-run job."}), 403
        if not path.exists() or not path.is_file():
            return jsonify({"error": f"Artifact not found: {raw_path}"}), 404
        return send_file(path)

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

    # ------------------------------------------------------------------
    # /optimizer — NinjaTrader optimizer web UI (Phase 1: plan preview)
    # ------------------------------------------------------------------

    _OPTIMIZER_COOKIE = "ta_optimizer_session_id"

    def _resolve_optimizer_session():
        from ta_foundation.web.optimizer_session import create_session, get_session

        cookie_sid = (request.cookies.get(_OPTIMIZER_COOKIE) or "").strip()
        session = get_session(cookie_sid) if cookie_sid else None
        set_cookie = False
        if session is None:
            session = create_session(instrument="NQ", market_suffix="NQ")
            set_cookie = True
        return session, set_cookie

    @app.route("/optimizer")
    def optimizer_page():
        session, set_cookie = _resolve_optimizer_session()
        response = app.make_response(
            render_template("optimizer.html", session_id=session.id)
        )
        if set_cookie:
            response.set_cookie(
                _OPTIMIZER_COOKIE,
                session.id,
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                samesite="Lax",
            )
        return response

    @app.route("/optimizer/sessions")
    def optimizer_sessions_page():
        from ta_foundation.web.optimizer_session import list_sessions

        active = (request.cookies.get(_OPTIMIZER_COOKIE) or "").strip()
        sessions = list_sessions()
        return render_template(
            "optimizer_sessions.html",
            sessions=sessions,
            active_session_id=active,
        )

    @app.route("/optimizer/sessions/<session_id>")
    def optimizer_session_detail_page(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_results import (
            OptimizerResultsError,
            load_optimizer_results,
        )

        session = get_session(session_id)
        if session is None:
            from flask import abort
            return abort(404)
        doc = session.load_document()
        plan = session.load_plan()
        summary = session.summary()
        # Try to add the latest parsed row count for pipeline-state display.
        try:
            results = load_optimizer_results(session, top_n=1)
            summary["row_count"] = results.row_count
        except OptimizerResultsError:
            summary["row_count"] = 0
        # Include the deployment package dir if it exists.
        pkg_dir = session.directory / "deployment_package"
        summary["package_dir"] = str(pkg_dir) if pkg_dir.exists() else None
        return render_template(
            "optimizer_session_detail.html",
            session=doc.to_dict(),
            plan=plan,
            summary=summary,
        )

    @app.route("/optimizer/sessions/<session_id>/resume")
    def optimizer_session_resume(session_id: str):
        from flask import redirect
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return redirect("/optimizer/sessions")
        response = app.make_response(redirect("/optimizer"))
        response.set_cookie(
            _OPTIMIZER_COOKIE,
            session.id,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="Lax",
        )
        return response

    @app.route("/api/optimizer/strategies")
    def api_optimizer_strategies():
        from ta_foundation.web.optimizer_strategy_catalog import list_strategies

        source = request.args.get("source_dir") or None
        templates = request.args.get("template_dir") or None
        rows = list_strategies(source_dir=source, template_dir=templates)
        return jsonify({"strategies": [r.to_dict() for r in rows]})

    @app.route("/api/optimizer/strategies/<strategy_id>")
    def api_optimizer_strategy_detail(strategy_id: str):
        from ta_foundation.web.optimizer_strategy_catalog import get_strategy_detail

        source = request.args.get("source_dir") or None
        templates = request.args.get("template_dir") or None
        detail = get_strategy_detail(
            strategy_id, source_dir=source, template_dir=templates
        )
        if detail is None:
            return jsonify({"error": f"unknown strategy: {strategy_id}"}), 404
        return jsonify(detail.to_dict())

    @app.route("/api/optimizer/sessions", methods=["GET"])
    def api_optimizer_sessions_list():
        from ta_foundation.web.optimizer_session import list_sessions

        return jsonify({"sessions": list_sessions()})

    @app.route("/api/optimizer/sessions", methods=["POST"])
    def api_optimizer_sessions_create():
        from ta_foundation.web.optimizer_session import create_session

        payload = request.get_json(silent=True) or {}
        session = create_session(
            label=str(payload.get("label") or ""),
            strategy_id=str(payload.get("strategy_id") or ""),
            seed_template_path=str(payload.get("seed_template_path") or ""),
            instrument=str(payload.get("instrument") or "NQ"),
            market_suffix=str(payload.get("market_suffix") or "NQ"),
        )
        return jsonify({"session": session.load_document().to_dict()})

    @app.route("/api/optimizer/sessions/<session_id>", methods=["GET"])
    def api_optimizer_session_get(session_id: str):
        from ta_foundation.web.optimizer_session import (
            OptimizerSessionError,
            get_session,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        try:
            doc = session.load_document().to_dict()
        except OptimizerSessionError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"session": doc, "plan": session.load_plan()})

    @app.route("/api/optimizer/sessions/<session_id>", methods=["PATCH"])
    def api_optimizer_session_patch(session_id: str):
        from ta_foundation.web.optimizer_session import (
            OptimizerSessionError,
            get_session,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        try:
            doc = session.update(**payload)
        except OptimizerSessionError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"session": doc.to_dict()})

    @app.route("/api/optimizer/sessions/<session_id>", methods=["DELETE"])
    def api_optimizer_session_delete(session_id: str):
        from ta_foundation.web.optimizer_session import delete_session

        removed = delete_session(session_id)
        return jsonify({"deleted": removed})

    @app.route(
        "/api/optimizer/sessions/<session_id>/templates/generate",
        methods=["POST"],
    )
    def api_optimizer_templates_generate(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_template_writer import (
            TemplateWriteError,
            generate_session_templates,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        try:
            written = generate_session_templates(
                session,
                optimizer_type=(payload.get("optimizer_type") or None),
                optimization_fitness=(payload.get("optimization_fitness") or None),
            )
        except TemplateWriteError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({
            "output_dir": str(session.directory / "generated_templates"),
            "templates": [t.to_dict() for t in written],
        })

    @app.route(
        "/api/optimizer/sessions/<session_id>/templates/rename",
        methods=["POST"],
    )
    def api_optimizer_templates_rename(session_id: str):
        from ta_foundation.web.optimizer_namer import NamerError, run_template_namer
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        input_dir = Path(payload.get("input_dir") or (session.directory / "generated_templates"))
        output_dir = Path(payload.get("output_dir") or (session.directory / "renamed_templates"))
        try:
            result = run_template_namer(
                input_dir=input_dir,
                output_dir=output_dir,
                market=str(payload.get("market") or "NQ"),
                template_naming_dir=payload.get("template_naming_dir"),
            )
        except NamerError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"result": result.to_dict()})

    @app.route(
        "/api/optimizer/sessions/<session_id>/clone",
        methods=["POST"],
    )
    def api_optimizer_session_clone(session_id: str):
        from ta_foundation.web.optimizer_session import clone_session, get_session

        source = get_session(session_id)
        if source is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        new = clone_session(source, label=payload.get("label"))
        return jsonify({"session": new.load_document().to_dict()})

    @app.route(
        "/api/optimizer/sessions/<session_id>/matches",
        methods=["GET"],
    )
    def api_optimizer_session_matches(session_id: str):
        from ta_foundation.web.optimizer_session import (
            get_session,
            list_sessions,
            get_storage_root,
            OptimizerSession,
            OptimizerSessionDocument,
        )
        import json as _json

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        try:
            doc = session.load_document()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        target_hash = doc.plan_hash()
        matches: list[dict] = []
        root = get_storage_root()
        if root.exists():
            for child in root.iterdir():
                if not child.is_dir() or child.name == session_id:
                    continue
                sess_path = child / OptimizerSession.SESSION_FILENAME
                if not sess_path.exists():
                    continue
                try:
                    with open(sess_path, encoding="utf-8") as h:
                        data = _json.load(h)
                    other = OptimizerSessionDocument.from_dict(data)
                except Exception:
                    continue
                if other.plan_hash() != target_hash:
                    continue
                summary = OptimizerSession(child).summary()
                matches.append({
                    "session_id": other.session_id,
                    "label": other.label,
                    "updated_at": other.updated_at,
                    "decision_state": summary.get("decision_state"),
                    "final_validation_status": summary.get("final_validation_status"),
                    "final_recommendation_count": summary.get("final_recommendation_count"),
                })
        matches.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
        return jsonify({"plan_hash": target_hash, "matches": matches})

    @app.route(
        "/api/optimizer/sessions/<session_id>/preflight",
        methods=["GET"],
    )
    def api_optimizer_preflight(session_id: str):
        from ta_foundation.web.optimizer_preflight import run_preflight
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        return jsonify({"preflight": run_preflight(session).to_dict()})

    @app.route(
        "/api/optimizer/sessions/<session_id>/plan/preview",
        methods=["POST"],
    )
    def api_optimizer_plan_preview(session_id: str):
        from ta_foundation.web.optimizer_plan import build_plan_preview
        from ta_foundation.web.optimizer_session import (
            OptimizerSessionError,
            get_session,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404

        payload = request.get_json(silent=True) or {}
        if payload:
            try:
                session.update(**payload)
            except OptimizerSessionError as exc:
                return jsonify({"error": str(exc)}), 400

        doc = session.load_document()
        plan = build_plan_preview(doc)
        plan_dict = plan.to_dict()
        session.save_plan(plan_dict)
        return jsonify({"plan": plan_dict})

    @app.route(
        "/api/optimizer/sessions/<session_id>/run",
        methods=["POST"],
    )
    def api_optimizer_run_start(session_id: str):
        from ta_foundation.web.optimizer_runner import (
            OptimizerRunnerError,
            start_run,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        try:
            record = start_run(session)
        except OptimizerRunnerError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"run": record.to_dict()})

    @app.route(
        "/api/optimizer/sessions/<session_id>/run/status",
        methods=["GET"],
    )
    def api_optimizer_run_status(session_id: str):
        from ta_foundation.web.optimizer_runner import get_status
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        status = get_status(session)
        if status is None:
            return jsonify({"status": None})
        return jsonify({"status": status.to_dict()})

    @app.route(
        "/api/optimizer/sessions/<session_id>/run/cancel",
        methods=["POST"],
    )
    def api_optimizer_run_cancel(session_id: str):
        from ta_foundation.web.optimizer_runner import cancel_run
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        record = cancel_run(session)
        if record is None:
            return jsonify({"error": "no run to cancel"}), 404
        return jsonify({"run": record.to_dict()})

    @app.route(
        "/api/optimizer/sessions/<session_id>/results",
        methods=["GET"],
    )
    def api_optimizer_results(session_id: str):
        from ta_foundation.web.optimizer_results import (
            OptimizerResultsError,
            load_optimizer_results,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        try:
            top_n = int(request.args.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            results = load_optimizer_results(session, top_n=max(1, min(top_n, 100)))
        except OptimizerResultsError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"results": results.to_dict()})

    @app.route(
        "/api/optimizer/sessions/<session_id>/deployment-package",
        methods=["POST"],
    )
    def api_optimizer_deployment_package(session_id: str):
        from ta_foundation.web.optimizer_deployment_package import (
            OptimizerDeploymentPackageError,
            build_deployment_package,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        try:
            top_n = int(payload.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            package = build_deployment_package(
                session,
                top_n=max(1, min(top_n, 100)),
                oos_from_date=(payload.get("oos_from_date") or None),
                oos_to_date=(payload.get("oos_to_date") or None),
                backtest_seed_template_path=(payload.get("backtest_seed_template_path") or None),
            )
        except OptimizerDeploymentPackageError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"package": package.to_dict()})

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


def _runs_with_live_status(session) -> list:
    """Merge persisted StageRun records with live JobManager status.

    The on-disk run.status reflects the moment the run was dispatched. The
    JobManager knows the current state of each subprocess. We pull both and
    return the live status when available; we also auto-promote the run on
    disk to a terminal status the first time we see it.
    """
    runs = session.list_runs()
    out: list[dict] = []
    for run in runs:
        live_job = _job_manager.get(run.job_id) if _job_manager else None
        merged = run.to_dict()
        if live_job is not None:
            merged["status"] = live_job.status
            merged["returncode"] = live_job.returncode
            merged["finished_at"] = live_job.finished_at or run.finished_at
            # Persist the terminal status once so future calls don't have to
            # cross-reference a long-gone job record.
            if live_job.status in {"succeeded", "failed"} and run.status not in {"succeeded", "failed"}:
                session.update_run_status(
                    run.job_id,
                    status=live_job.status,
                    finished_at=live_job.finished_at,
                )
        out.append(merged)
    return out


def _build_discovery_cli_command(
    *,
    input_folder: str | None,
    output_folder: str | None,
    market_data_folder: str | None,
    report_config_path: str,
    recursive: bool,
    no_tick_data: bool,
) -> list[str]:
    """Build the resolved CLI argv for a discovery run dispatch."""
    if not input_folder or not output_folder:
        raise ValueError("input_folder and output_folder are required to dispatch a run")
    cmd = [
        sys.executable,
        "-m",
        "ta_foundation.cli.main",
        "--input",
        str(input_folder),
        "--output",
        str(output_folder),
        "--report-config",
        str(report_config_path),
    ]
    if market_data_folder:
        cmd.extend(["--market-data", str(market_data_folder)])
    if recursive:
        cmd.append("--recursive")
    if no_tick_data:
        cmd.append("--no-tick-data")
    return cmd


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
