from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file
from ta_foundation.web.report_assets import resolve_path_under_root, resolve_report_asset_path


_OPTIMIZER_COOKIE = "ta_optimizer_session_id"
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_TEMPLATE_DIR),
        static_folder=str(_STATIC_DIR),
    )

    @app.route("/")
    def index():
        return redirect("/optimizer/weekly-coverage", code=302)

    @app.route("/optimizer")
    @app.route("/optimizer/recipe")
    @app.route("/optimizer/deployment-matrix")
    def optimizer_redirects():
        return redirect("/optimizer/weekly-coverage", code=302)

    @app.route("/optimizer/weekly-coverage")
    def optimizer_weekly_coverage_page():
        return render_template("optimizer_weekly_coverage.html")

    @app.route("/optimizer/sessions")
    def optimizer_sessions_page():
        active = (request.cookies.get(_OPTIMIZER_COOKIE) or "").strip()
        return render_template(
            "optimizer_weekly_sessions.html",
            sessions=_list_weekly_sessions(),
            active_session_id=active,
        )

    @app.route("/reports")
    @app.route("/reports/")
    def optimizer_public_reports_page():
        from ta_foundation.web.weekly_report_publish import published_site_index_path

        published_index = published_site_index_path()
        if published_index.exists():
            return redirect("/reports/index.html", code=302)
        return render_template(
            "optimizer_weekly_public_reports.html",
            sessions=_list_weekly_published_reports(),
        )

    @app.route("/reports/<path:filename>")
    def optimizer_public_reports_file(filename: str):
        from ta_foundation.web.weekly_report_publish import published_site_root

        root = published_site_root()
        target = resolve_path_under_root(root, filename)
        if target is None or not target.exists():
            return abort(404)
        if target.suffix.lower() == ".html":
            return send_file(target.resolve(), mimetype="text/html")
        return send_file(target.resolve())

    @app.route("/optimizer/sessions/<session_id>")
    def optimizer_session_detail_page(session_id: str):
        from ta_foundation.web.optimizer_results import (
            OptimizerResultsError,
            load_optimizer_results,
        )
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            weekly_coverage_report_path,
            weekly_coverage_zip_path,
        )
        from ta_foundation.web.optimizer_weekly_report_pack import (
            weekly_report_pack_index_path,
            weekly_report_pack_zip_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)

        doc = session.load_document()
        summary = session.summary()
        state = _load_recipe_state(session)
        summary["recipe_state"] = state.get("state") or ""
        summary["current_stage_id"] = state.get("current_stage_id") or ""
        summary["last_error"] = state.get("last_error") or ""
        summary["has_final_review"] = (
            session.directory
            / "deployment_package"
            / "final_backtest_handoff"
            / "final_backtest_review"
        ).exists()
        summary["weekly_coverage_report_url"] = (
            f"/optimizer/sessions/{session.id}/weekly-coverage-package/report"
            if weekly_coverage_report_path(session).exists()
            else None
        )
        summary["weekly_coverage_zip_url"] = (
            f"/optimizer/sessions/{session.id}/weekly-coverage-package.zip"
            if weekly_coverage_zip_path(session).exists()
            else None
        )
        session_report = session.directory / "deployment_package" / "session_candidate_report.html"
        summary["session_candidate_report_url"] = (
            f"/optimizer/sessions/{session.id}/candidate-report"
            if session_report.exists()
            else None
        )
        daily_update = (
            session.directory
            / "deployment_package"
            / "weekly_coverage_package"
            / "reports"
            / "weekly_strategy_daily_update_report.html"
        )
        summary["daily_update_report_url"] = (
            f"/optimizer/sessions/{session.id}/weekly-daily-update-report"
            if daily_update.exists()
            else None
        )
        weekly_pack_index = weekly_report_pack_index_path(session)
        weekly_pack_zip = weekly_report_pack_zip_path(session)
        summary["weekly_reports_url"] = (
            f"/optimizer/sessions/{session.id}/weekly-reports"
            if weekly_pack_index.exists()
            else None
        )
        summary["weekly_reports_zip_url"] = (
            f"/optimizer/sessions/{session.id}/weekly-reports.zip"
            if weekly_pack_zip.exists()
            else None
        )
        summary["weekly_reports_publish_ready"] = (
            weekly_pack_index.exists() or weekly_pack_zip.exists()
        )
        summary["publish_portal_url"] = f"/reports#{session.id}"
        from ta_foundation.web.weekly_report_publish import published_entry_for_session

        published = published_entry_for_session(session.id)
        summary["published_week_url"] = (
            f"/reports/{published.get('week_page_href')}"
            if isinstance(published, dict) and published.get("week_page_href")
            else None
        )
        try:
            results = load_optimizer_results(session, top_n=1)
            summary["row_count"] = results.row_count
        except OptimizerResultsError:
            summary["row_count"] = 0
        return render_template(
            "optimizer_weekly_session.html",
            session=doc.to_dict(),
            summary=summary,
        )

    @app.route("/optimizer/sessions/<session_id>/resume")
    def optimizer_session_resume(session_id: str):
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return redirect("/optimizer/sessions", code=302)
        response = app.make_response(redirect(f"/optimizer/sessions/{session_id}", code=302))
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

    @app.route(
        "/api/optimizer/strategies/<strategy_id>/regenerate-seed",
        methods=["POST"],
    )
    def api_optimizer_regenerate_seed(strategy_id: str):
        from ta_foundation.web.optimizer_strategy_catalog import (
            RecipeSeedRegenerationError,
            regenerate_recipe_seed,
        )

        payload = request.get_json(silent=True) or {}
        try:
            summary = regenerate_recipe_seed(
                strategy_id,
                instrument=(payload.get("instrument") or "").strip() or None,
                from_date=(payload.get("from_date") or "").strip() or None,
                to_date=(payload.get("to_date") or "").strip() or None,
                template_dir=(payload.get("template_dir") or "").strip() or None,
            )
        except RecipeSeedRegenerationError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"unexpected error: {exc}"}), 500
        return jsonify({"seed": summary.to_dict()})

    @app.route("/api/optimizer/weekly-coverage/run", methods=["POST"])
    def api_optimizer_weekly_coverage_run():
        from ta_foundation.web.optimizer_recipe import save_recipe
        from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
        from ta_foundation.web.optimizer_recipe_plan import build_and_save_recipe_plan
        from ta_foundation.web.optimizer_session import create_session

        payload = request.get_json(silent=True) or {}
        strategy_id = str(payload.get("strategy_id") or "").strip()
        seed_template_path = str(payload.get("seed_template_path") or "").strip()
        if not strategy_id:
            return jsonify({"error": "Pick a strategy first."}), 400
        if not seed_template_path:
            return jsonify({"error": "Pick a seed template first."}), 400

        instrument = str(payload.get("instrument") or "NQ 06-26").strip() or "NQ 06-26"
        market_suffix = str(payload.get("market_suffix") or "NQ").strip() or "NQ"
        label = str(payload.get("label") or "Weekly Coverage").strip() or "Weekly Coverage"

        session = create_session(
            label=label,
            strategy_id=strategy_id,
            seed_template_path=seed_template_path,
            instrument=instrument,
            market_suffix=market_suffix,
        )
        session.update(
            oos_from_date=str(payload.get("start_date") or ""),
            oos_to_date=str(payload.get("end_date") or ""),
            guardrails={
                "min_trades": _payload_number(payload, "min_trades", 20),
                "min_profit_factor": _payload_number(payload, "min_profit_factor", 1.2),
                "max_drawdown_dollars": _payload_number(payload, "max_drawdown", 2500),
                "min_net_profit": _payload_number(payload, "min_net_profit", 0),
                "min_percent_days_traded": _payload_number(payload, "min_percent_days_traded", 20),
            },
            chunking={
                "max_combinations_per_chunk": _payload_number(payload, "max_combinations_per_chunk", 5000),
                "max_runtime_minutes_per_chunk": _payload_number(payload, "max_runtime_minutes_per_chunk", 180),
                "keep_best_results": _payload_number(payload, "keep_best_results", 1000),
            },
        )

        recipe = _weekly_coverage_recipe_payload(
            strategy_id=strategy_id,
            recipe_name=label,
            start_hours=_payload_int_list(payload, "start_hours", [0, 4, 8, 12, 16, 20]),
            duration_hours=_payload_int(payload, "duration_hours", 4),
            slow_ma_values=_payload_int_list(payload, "slow_ma_values", [20, 50, 100, 200, 300, 400]),
            final_per_lane=_payload_int(payload, "final_per_lane", 2),
            min_trades=_payload_number(payload, "min_trades", 20),
            min_profit_factor=_payload_number(payload, "min_profit_factor", 1.2),
            max_drawdown=_payload_number(payload, "max_drawdown", 2500),
            min_net_profit=_payload_number(payload, "min_net_profit", 0),
            average_fast_min=_payload_number(payload, "average_fast_min", 5),
            average_fast_max=_payload_number(payload, "average_fast_max", 5),
            average_fast_step=_payload_number(payload, "average_fast_step", 1),
            max_stop_min=_payload_number(payload, "max_stop_min", 50),
            max_stop_max=_payload_number(payload, "max_stop_max", 350),
            max_stop_step=_payload_number(payload, "max_stop_step", 50),
            max_tp_ratio_min=_payload_number(payload, "max_tp_ratio_min", 0.5),
            max_tp_ratio_max=_payload_number(payload, "max_tp_ratio_max", 2.0),
            max_tp_ratio_step=_payload_number(payload, "max_tp_ratio_step", 0.5),
            auto_refine_risk=bool(payload.get("auto_refine_risk", True)),
            profit_stop_min=_payload_number(payload, "profit_stop_min", 1),
            profit_stop_max=_payload_number(payload, "profit_stop_max", 1001),
            profit_stop_step=_payload_number(payload, "profit_stop_step", 500),
            loss_stop_min=_payload_number(payload, "loss_stop_min", 1),
            loss_stop_max=_payload_number(payload, "loss_stop_max", 1001),
            loss_stop_step=_payload_number(payload, "loss_stop_step", 500),
            max_trades_min=_payload_number(payload, "max_trades_min", 1),
            max_trades_max=_payload_number(payload, "max_trades_max", 11),
            max_trades_step=_payload_number(payload, "max_trades_step", 2),
        )
        try:
            save_recipe(session, recipe)
            plan = build_and_save_recipe_plan(session)
            status = RecipeRunOrchestrator(session).start()
        except Exception as exc:
            return jsonify({"error": str(exc), "session": session.load_document().to_dict()}), 400

        response = jsonify(
            {
                "session": session.load_document().to_dict(),
                "recipe": recipe,
                "plan": plan.to_dict(),
                "status": status,
                "urls": {
                    "session": f"/optimizer/sessions/{session.id}",
                    "recipe": f"/optimizer/sessions/{session.id}/resume",
                    "results": f"/optimizer/sessions/{session.id}",
                },
            }
        )
        response.set_cookie(
            _OPTIMIZER_COOKIE,
            session.id,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="Lax",
        )
        return response

    @app.route("/api/optimizer/weekly-coverage/recent", methods=["GET"])
    def api_optimizer_weekly_coverage_recent():
        return jsonify({"sessions": _list_weekly_sessions()[:60]})

    @app.route(
        "/api/optimizer/sessions/<session_id>/weekly-coverage-package",
        methods=["POST"],
    )
    def api_optimizer_weekly_coverage_package(session_id: str):
        from ta_foundation.web.optimizer_candidate_report import (
            build_session_candidate_report,
        )
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            WeeklyCoverageConfig,
            WeeklyCoveragePackageError,
            build_weekly_coverage_package,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        report_asset_mode = str(payload.get("report_asset_mode") or "embedded")
        try:
            result = build_weekly_coverage_package(
                session,
                config=WeeklyCoverageConfig.from_session(session, payload),
            )
            build_session_candidate_report(
                session,
                images_dir=session.load_document().god_images_dir or None,
                report_asset_mode=report_asset_mode,
            )
        except WeeklyCoveragePackageError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"unexpected weekly package build error: {exc}"}), 500
        std_report = session.directory / "deployment_package" / "session_candidate_report.html"
        return jsonify(
            {
                "result": result.to_dict(),
                "standard_report_exists": std_report.exists(),
                "standard_report_url": f"/optimizer/sessions/{session_id}/candidate-report",
                "category_bundle_url": f"/optimizer/sessions/{session_id}/category-bundle",
            }
        )

    @app.route(
        "/api/optimizer/sessions/<session_id>/weekly-daily-update-report",
        methods=["POST"],
    )
    def api_optimizer_weekly_daily_update_report(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            WeeklyCoverageConfig,
            WeeklyCoveragePackageError,
            build_weekly_daily_update_report,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        try:
            result = build_weekly_daily_update_report(
                session,
                config=WeeklyCoverageConfig.from_session(session, payload),
                include_fallbacks=bool(payload.get("include_fallbacks", False)),
                target_date=payload.get("target_date") or None,
                report_asset_mode=str(payload.get("report_asset_mode") or "embedded"),
            )
        except WeeklyCoveragePackageError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"unexpected daily update report build error: {exc}"}), 500
        return jsonify(
            {
                "result": result.to_dict(),
                "report_url": f"/optimizer/sessions/{session_id}/weekly-daily-update-report",
            }
        )

    @app.route(
        "/api/optimizer/sessions/<session_id>/weekly-reports",
        methods=["POST"],
    )
    def api_optimizer_weekly_reports(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_report_pack import (
            WeeklyReportPackError,
            build_weekly_report_pack,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        raw_run_ids = payload.get("run_ids")
        run_ids = (
            [str(run_id) for run_id in raw_run_ids if str(run_id).strip()]
            if isinstance(raw_run_ids, list)
            else None
        )
        try:
            result = build_weekly_report_pack(
                session,
                run_ids=run_ids,
                include_all_active_templates=bool(payload.get("include_all_active_templates", False)),
                report_asset_mode=str(payload.get("report_asset_mode") or "embedded"),
            )
        except WeeklyReportPackError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"unexpected weekly reports build error: {exc}"}), 500
        return jsonify(
            {
                "result": result.to_dict(),
                "report_url": f"/optimizer/sessions/{session_id}/weekly-reports",
                "zip_url": f"/optimizer/sessions/{session_id}/weekly-reports.zip",
            }
        )

    @app.route(
        "/api/optimizer/sessions/<session_id>/publish-site",
        methods=["POST"],
    )
    def api_optimizer_publish_site(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.weekly_report_publish import (
            PublishedReportError,
            publish_weekly_session,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        try:
            result = publish_weekly_session(
                session,
                title=str(payload.get("title") or "").strip() or None,
            )
        except PublishedReportError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"unexpected publish-site error: {exc}"}), 500
        return jsonify(
            {
                "result": result.to_dict(),
                "archive_url": "/reports/index.html",
                "week_url": f"/reports/{result.week_page_href}",
            }
        )

    @app.route("/api/optimizer/sessions/<session_id>/category-bundle", methods=["GET"])
    def api_optimizer_category_bundle(session_id: str):
        from ta_foundation.web.optimizer_category_bundle import compute_category_bundle
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        try:
            view = compute_category_bundle(session)
        except Exception as exc:
            return jsonify({"error": f"category bundle error: {exc}"}), 500
        return jsonify(view.to_dict())

    @app.route("/api/optimizer/sessions/<session_id>/category-bundle/build", methods=["POST"])
    def api_optimizer_category_bundle_build(session_id: str):
        from ta_foundation.web.optimizer_category_bundle import (
            CategoryBundleError,
            build_pruned_bundle,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        keep = payload.get("keep_run_ids")
        if not isinstance(keep, list):
            return jsonify({"error": "keep_run_ids (list) is required"}), 400
        try:
            result = build_pruned_bundle(session, [str(r) for r in keep])
        except CategoryBundleError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"pruned bundle build error: {exc}"}), 500
        return jsonify({"result": result})

    @app.route("/optimizer/sessions/<session_id>/category-bundle", methods=["GET"])
    def optimizer_category_bundle_page(session_id: str):
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return abort(404)
        return render_template("optimizer_category_bundle.html", session_id=session_id)

    @app.route("/optimizer/sessions/<session_id>/category-bundle.zip", methods=["GET"])
    def optimizer_category_bundle_zip(session_id: str):
        from ta_foundation.web.optimizer_category_bundle import pruned_bundle_zip_path
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return abort(404)
        zip_path = pruned_bundle_zip_path(session)
        if not zip_path.exists():
            return abort(404)
        return send_file(zip_path.resolve(), as_attachment=True, download_name=zip_path.name)

    @app.route("/api/optimizer/sessions/<session_id>/refine/candidates", methods=["GET"])
    def api_optimizer_refine_candidates(session_id: str):
        from ta_foundation.web.optimizer_refinement import (
            RefinementError,
            list_refinable_candidates,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        try:
            candidates = list_refinable_candidates(session)
        except RefinementError as exc:
            return jsonify({"error": str(exc), "candidates": []}), 400
        return jsonify({"candidates": candidates})

    @app.route("/api/optimizer/sessions/<session_id>/refine", methods=["POST"])
    def api_optimizer_refine(session_id: str):
        from ta_foundation.web.optimizer_recipe import load_recipe
        from ta_foundation.web.optimizer_recipe_state import (
            RecipeRunState,
            append_recipe_event,
            load_recipe_state,
            save_recipe_state,
        )
        from ta_foundation.web.optimizer_refinement import (
            RefinementError,
            RefinementRanges,
            prepare_refinement,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        run_ids = payload.get("candidate_run_ids")
        if not isinstance(run_ids, list) or not run_ids:
            return jsonify({"error": "candidate_run_ids (non-empty list) is required"}), 400
        try:
            prep = prepare_refinement(
                session,
                [str(r) for r in run_ids],
                ranges=RefinementRanges.from_payload(payload.get("ranges")),
                keep_per_candidate=_payload_int(payload, "keep_per_candidate", 2),
            )
            recipe = load_recipe(session)
            state = load_recipe_state(session) or RecipeRunState(
                recipe_id=recipe.recipe_id, state="generating_child_stage",
            )
            state.state = "generating_child_stage"
            state.current_stage_id = prep.refine_stage_id
            state.last_error = None
            state.pause_requested = False
            state.stop_requested = False
            save_recipe_state(session, state)
            append_recipe_event(
                session,
                event_type="refinement_launched",
                recipe_id=recipe.recipe_id,
                stage_id=prep.refine_stage_id,
                message=(
                    f"Refinement launched on {prep.candidate_count} candidate(s); "
                    f"{prep.combos_per_candidate} risk combos each."
                ),
            )
        except RefinementError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"refinement launch error: {exc}"}), 500
        return jsonify(
            {
                "refine_stage_id": prep.refine_stage_id,
                "final_stage_id": prep.final_stage_id,
                "candidate_count": prep.candidate_count,
                "combos_per_candidate": prep.combos_per_candidate,
                "total_combinations": prep.total_combinations,
                "pinned_params": prep.pinned_params,
            }
        )

    @app.route("/api/optimizer/sessions/<session_id>/weekly-coverage/lanes", methods=["GET"])
    def api_optimizer_weekly_coverage_lanes(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            weekly_coverage_package_dir,
        )

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        lanes_path = (
            weekly_coverage_package_dir(session)
            / "data"
            / "operationally_diverse_lane_coverage.csv"
        )
        if not lanes_path.exists():
            return jsonify({"lanes": [], "built": False})

        def _as_int(value: Any) -> int:
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        rows: list[dict[str, Any]] = []
        with lanes_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                rows.append(
                    {
                        "bucket": row.get("bucket", ""),
                        "side": row.get("side", ""),
                        "slowMA": row.get("slowMA", ""),
                        "naming_family": row.get("naming_family", ""),
                        "validated": _as_int(row.get("operationally_diverse_count")),
                        "fallback": _as_int(row.get("fallback_count")),
                        "passed": _as_int(row.get("passed_count")),
                        "run_ids": row.get("operationally_diverse_run_ids", ""),
                    }
                )
        return jsonify({"lanes": rows, "built": True})

    @app.route("/optimizer/sessions/<session_id>/refine", methods=["GET"])
    def optimizer_refine_page(session_id: str):
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return abort(404)
        return render_template("optimizer_refine.html", session_id=session_id)

    @app.route(
        "/api/optimizer/sessions/<session_id>/recipe/advance",
        methods=["POST"],
    )
    def api_optimizer_recipe_advance(session_id: str):
        from ta_foundation.web.optimizer_recipe import OptimizerRecipeError
        from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
        from ta_foundation.web.optimizer_recipe_runner import RecipeRunnerError
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404

        try:
            status = RecipeRunOrchestrator(session).advance_once()
        except OptimizerRecipeError as exc:
            return jsonify({"error": str(exc)}), 400
        except RecipeRunnerError as exc:
            return jsonify({"error": str(exc), "retryable": True}), 409

        return jsonify(status)

    @app.route("/optimizer/sessions/<session_id>/candidate-report")
    def optimizer_session_candidate_report_page(session_id: str):
        from ta_foundation.web.optimizer_candidate_report import (
            session_candidate_report_path,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return abort(404)
        html_path = session_candidate_report_path(session)
        if not html_path.exists():
            return abort(404)
        return send_file(html_path.resolve(), mimetype="text/html")

    @app.route("/optimizer/sessions/<session_id>/candidate-report_assets/<path:filename>")
    def optimizer_session_candidate_report_asset(session_id: str, filename: str):
        from ta_foundation.web.optimizer_candidate_report import (
            session_candidate_report_path,
        )
        from ta_foundation.web.optimizer_session import get_session

        session = get_session(session_id)
        if session is None:
            return abort(404)
        asset_path = resolve_report_asset_path(session_candidate_report_path(session), filename)
        if asset_path is None:
            return abort(404)
        return send_file(asset_path.resolve())

    @app.route("/optimizer/sessions/<session_id>/weekly-coverage-package/report")
    def optimizer_weekly_coverage_package_report_page(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            weekly_coverage_report_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        html_path = weekly_coverage_report_path(session)
        if not html_path.exists():
            return abort(404)
        return send_file(html_path.resolve(), mimetype="text/html")

    @app.route("/optimizer/sessions/<session_id>/weekly-coverage-package/report_assets/<path:filename>")
    def optimizer_weekly_coverage_package_report_asset(session_id: str, filename: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            weekly_coverage_report_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        asset_path = resolve_report_asset_path(weekly_coverage_report_path(session), filename)
        if asset_path is None:
            return abort(404)
        return send_file(asset_path.resolve())

    @app.route("/optimizer/sessions/<session_id>/weekly-reports")
    def optimizer_weekly_reports_page(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_report_pack import (
            weekly_report_pack_index_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        html_path = weekly_report_pack_index_path(session)
        if not html_path.exists():
            return abort(404)
        return send_file(html_path.resolve(), mimetype="text/html")

    @app.route("/optimizer/sessions/<session_id>/weekly-reports.zip")
    def optimizer_weekly_reports_zip(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_report_pack import (
            weekly_report_pack_zip_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        zip_path = weekly_report_pack_zip_path(session)
        if not zip_path.exists():
            return abort(404)
        return send_file(zip_path.resolve(), as_attachment=True, download_name=zip_path.name)

    @app.route("/optimizer/sessions/<session_id>/weekly-reports/files/<path:filename>")
    def optimizer_weekly_reports_file(session_id: str, filename: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_report_pack import (
            weekly_report_pack_dir,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        target = resolve_path_under_root(weekly_report_pack_dir(session), filename)
        if target is None:
            return abort(404)
        if target.suffix.lower() == ".html":
            return send_file(target.resolve(), mimetype="text/html")
        return send_file(target.resolve())

    @app.route("/optimizer/sessions/<session_id>/weekly-daily-update-report")
    def optimizer_weekly_daily_update_report_page(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            weekly_daily_update_report_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        html_path = weekly_daily_update_report_path(session)
        if not html_path.exists():
            return abort(404)
        return send_file(html_path.resolve(), mimetype="text/html")

    @app.route("/optimizer/sessions/<session_id>/weekly-daily-update-report_assets/<path:filename>")
    def optimizer_weekly_daily_update_report_asset(session_id: str, filename: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            weekly_daily_update_report_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        asset_path = resolve_report_asset_path(weekly_daily_update_report_path(session), filename)
        if asset_path is None:
            return abort(404)
        return send_file(asset_path.resolve())

    @app.route("/optimizer/sessions/<session_id>/weekly-coverage-package.zip")
    def optimizer_weekly_coverage_package_zip(session_id: str):
        from ta_foundation.web.optimizer_session import get_session
        from ta_foundation.web.optimizer_weekly_coverage_package import (
            weekly_coverage_zip_path,
        )

        session = get_session(session_id)
        if session is None:
            return abort(404)
        zip_path = weekly_coverage_zip_path(session)
        if not zip_path.exists():
            return abort(404)
        return send_file(
            zip_path.resolve(),
            mimetype="application/zip",
            as_attachment=True,
            download_name=zip_path.name,
        )

    return app


def _list_weekly_sessions() -> list[dict[str, Any]]:
    from ta_foundation.web.optimizer_session import get_session, list_sessions

    out: list[dict[str, Any]] = []
    for summary in list_sessions():
        sid = summary.get("session_id")
        session = get_session(sid) if sid else None
        if session is None:
            continue
        recipe = _load_recipe_json(session.directory)
        if not recipe or not _is_weekly_recipe(recipe):
            continue
        state = _load_recipe_state(session)
        out.append(
            {
                **summary,
                "state": state.get("state") or "",
                "current_stage_id": state.get("current_stage_id") or "",
                "final_review_exists": (
                    session.directory
                    / "deployment_package"
                    / "final_backtest_handoff"
                    / "final_backtest_review"
                ).exists(),
            }
        )
    return out


def _list_weekly_published_reports() -> list[dict[str, Any]]:
    from ta_foundation.web.optimizer_candidate_report import session_candidate_report_path
    from ta_foundation.web.optimizer_session import get_session, list_sessions
    from ta_foundation.web.optimizer_weekly_coverage_package import (
        weekly_coverage_report_path,
        weekly_daily_update_report_path,
    )
    from ta_foundation.web.optimizer_weekly_report_pack import weekly_report_pack_index_path

    out: list[dict[str, Any]] = []
    for summary in _list_weekly_sessions():
        sid = summary.get("session_id")
        session = get_session(sid) if sid else None
        if session is None:
            continue
        report_links: list[dict[str, str]] = []
        if session_candidate_report_path(session).exists():
            report_links.append({
                "label": "Standard report",
                "url": f"/optimizer/sessions/{session.id}/candidate-report",
            })
        if weekly_coverage_report_path(session).exists():
            report_links.append({
                "label": "Coverage package report",
                "url": f"/optimizer/sessions/{session.id}/weekly-coverage-package/report",
            })
        if weekly_daily_update_report_path(session).exists():
            report_links.append({
                "label": "Daily update report",
                "url": f"/optimizer/sessions/{session.id}/weekly-daily-update-report",
            })
        if weekly_report_pack_index_path(session).exists():
            report_links.append({
                "label": "Weekly report pack",
                "url": f"/optimizer/sessions/{session.id}/weekly-reports",
            })
        if not report_links:
            continue
        out.append({
            **summary,
            "report_links": report_links,
            "session_url": f"/optimizer/sessions/{session.id}",
        })
    return out


def _load_recipe_json(session_dir: Path) -> dict[str, Any] | None:
    path = session_dir / "recipe.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_recipe_state(session) -> dict[str, Any]:
    path = session.directory / "recipe_state.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_weekly_recipe(recipe: dict[str, Any]) -> bool:
    recipe_id = str(recipe.get("recipe_id") or "")
    if "weekly" in recipe_id.lower():
        return True
    for stage in recipe.get("stages") or []:
        if str(((stage or {}).get("selection") or {}).get("mode") or "") == "coverage_matrix_sequence":
            return True
    return False


def _payload_number(payload: dict[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _payload_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _payload_int_list(payload: dict[str, Any], key: str, default: list[int]) -> list[int]:
    value = payload.get(key)
    if isinstance(value, list):
        out: list[int] = []
        for item in value:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out or list(default)
    return list(default)


def _weekly_coverage_recipe_payload(
    *,
    strategy_id: str,
    recipe_name: str,
    start_hours: list[int],
    duration_hours: int,
    slow_ma_values: list[int],
    final_per_lane: int,
    min_trades: float,
    min_profit_factor: float,
    max_drawdown: float,
    min_net_profit: float,
    average_fast_min: float,
    average_fast_max: float,
    average_fast_step: float,
    max_stop_min: float,
    max_stop_max: float,
    max_stop_step: float,
    max_tp_ratio_min: float,
    max_tp_ratio_max: float,
    max_tp_ratio_step: float,
    auto_refine_risk: bool = True,
    profit_stop_min: float = 1,
    profit_stop_max: float = 1001,
    profit_stop_step: float = 500,
    loss_stop_min: float = 1,
    loss_stop_max: float = 1001,
    loss_stop_step: float = 500,
    max_trades_min: float = 1,
    max_trades_max: float = 11,
    max_trades_step: float = 2,
) -> dict[str, Any]:
    safe_name = "".join(ch.lower() if ch.isalnum() else "_" for ch in recipe_name).strip("_")
    recipe_id = f"rec_{safe_name or 'weekly_coverage'}"
    slow_ma_values = sorted({int(v) for v in slow_ma_values if int(v) >= 0}) or [20, 50, 100, 200, 300, 400]
    start_hours = sorted({int(v) for v in start_hours if 0 <= int(v) <= 23}) or [0, 4, 8, 12, 16, 20]
    duration_hours = max(1, int(duration_hours))

    structural_pins = [
        "StartTimeH", "DurationTimeH", "Reverse", "averageSlow",
        "averageFast", "MaxStop", "MaxTPRatio", "Long", "Short",
        "UseTrend", "UseTrendReverse",
    ]
    refine_risk_stage = {
        "stage_id": "refine_risk",
        "stage_type": "optimizer",
        "from": "stage_1.selected_rows",
        "description": "Auto-refine risk knobs (ProfitStop / LossStop / MaxTrades) on each lane winner.",
        "pin": list(structural_pins),
        "optimize_inside_template": {
            "ProfitStop": {"min": profit_stop_min, "max": profit_stop_max, "step": profit_stop_step},
            "LossStop": {"min": loss_stop_min, "max": loss_stop_max, "step": loss_stop_step},
            "MaxTrades": {"min": max_trades_min, "max": max_trades_max, "step": max_trades_step},
        },
        "selection": {
            "group_by": ["parent_candidate_id"],
            "keep_per_group": 1,
            "fitness_metrics": ["profit_factor", "total_net_profit"],
        },
    }
    final_from = "refine_risk.selected_rows" if auto_refine_risk else "stage_1.selected_rows"
    middle_stages = [refine_risk_stage] if auto_refine_risk else []
    return {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": recipe_id,
        "recipe_name": recipe_name,
        "strategy_id": strategy_id,
        "entries_per_direction": 1,
        "target_final_candidates": max(1, final_per_lane),
        "safety_caps": {
            "max_total_combinations": 250000,
            "max_templates_per_stage": 250,
        },
        "base_matrix": [
            {"param": "StartTimeH", "role": "matrix_axis", "values": list(start_hours)},
            {"param": "DurationTimeH", "role": "fixed", "value": duration_hours},
            {"param": "Reverse", "role": "matrix_axis", "values": [False, True]},
            {"param": "averageSlow", "role": "matrix_axis", "values": list(slow_ma_values)},
            {"param": "UseTrend", "role": "fixed", "value": False},
            {"param": "UseTrendReverse", "role": "fixed", "value": False},
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "description": "Weekly coverage broad search",
                "optimize_inside_template": {
                    "averageFast": {
                        "min": average_fast_min,
                        "max": average_fast_max,
                        "step": average_fast_step,
                    },
                    "MaxStop": {
                        "min": max_stop_min,
                        "max": max_stop_max,
                        "step": max_stop_step,
                    },
                    "MaxTPRatio": {
                        "min": max_tp_ratio_min,
                        "max": max_tp_ratio_max,
                        "step": max_tp_ratio_step,
                    },
                },
                "add_optimize": {
                    "Long": [False, True],
                    "Short": [False, True],
                },
                "selection": {
                    "mode": "coverage_matrix_sequence",
                    "group_by": ["StartTimeH", "Reverse", "averageSlow"],
                    "coverage_grid": {
                        "StartTimeH": list(start_hours),
                        "Reverse": [False, True],
                        "averageSlow": list(slow_ma_values),
                    },
                    "keep_per_group": max(1, final_per_lane),
                    "fitness_metrics": ["profit_factor", "total_net_profit"],
                    "min_trades": min_trades,
                    "min_profit_factor": min_profit_factor,
                    "max_drawdown": max_drawdown,
                    "min_net_profit": min_net_profit,
                },
            },
            *middle_stages,
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": final_from,
                "finalists_per_bucket": max(1, final_per_lane),
                "description": "Final fixed Backtest validation",
            },
        ],
        "optimizer_type": "Default",
        "keep_best_results": 1000,
        "active_targets": ["MaxProfitFactor", "MaxNetProfit"],
    }
