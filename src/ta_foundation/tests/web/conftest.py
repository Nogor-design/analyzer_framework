"""Shared fixtures for the web optimizer test suite.

The NinjaTrader IPC bridge file (``C:\\temp\\nt8_command.json``) is a real,
process-wide resource: a running NinjaTrader AddOn watches it and will
execute whatever ``RunBatch`` command it finds. Any test that exercises a
dispatch path (e.g. the ``/shortlist/promote`` endpoint, which dispatches
by default) must therefore never touch the production location, or it will
fire a real backtest against an ephemeral pytest temp folder.

``optimizer_runner`` defines the bridge constants; ``optimizer_recipe_runner``
and ``optimizer_promotion_run`` ``from ... import`` them at module load, so
each module holds its own binding that must be patched independently.
Modules that import the constant inside a function body (``optimizer_shadow``,
``optimizer_neighborhood``, ``optimizer_walkforward``) read it from
``optimizer_runner`` at call time and are covered by patching that module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web import optimizer_recipe_runner, optimizer_runner
from ta_foundation.web.weekly_report_publish import set_published_site_root

# optimizer_promotion_run is part of the (currently in-flight) promotion
# feature; import defensively so this conftest still loads — and still
# protects the recipe/runner paths — on checkouts where that module is absent.
try:
    from ta_foundation.web import optimizer_promotion_run as _optimizer_promotion_run
except ImportError:  # pragma: no cover - module not present on this checkout
    _optimizer_promotion_run = None


@pytest.fixture(autouse=True)
def isolate_nt_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the NT command/status files to a per-test temp location.

    Autouse so no web test can ever write to the live ``C:\\temp`` bridge,
    regardless of whether it remembered to pass ``command_file=``.
    """
    bridge = tmp_path / "_nt_bridge"
    bridge.mkdir(parents=True, exist_ok=True)
    command_file = bridge / "nt8_command.json"
    status_file = bridge / "nt8_status.json"

    modules = [optimizer_runner, optimizer_recipe_runner]
    if _optimizer_promotion_run is not None:
        modules.append(_optimizer_promotion_run)
    for module in modules:
        if hasattr(module, "DEFAULT_COMMAND_FILE"):
            monkeypatch.setattr(module, "DEFAULT_COMMAND_FILE", command_file, raising=False)
        if hasattr(module, "DEFAULT_STATUS_FILE"):
            monkeypatch.setattr(module, "DEFAULT_STATUS_FILE", status_file, raising=False)
    # ``optimizer_runner`` also compares against REAL_NT_COMMAND_FILE to decide
    # whether a command file belongs to the real bridge; keep it consistent.
    if hasattr(optimizer_runner, "REAL_NT_COMMAND_FILE"):
        monkeypatch.setattr(
            optimizer_runner, "REAL_NT_COMMAND_FILE", command_file, raising=False
        )

    return bridge


@pytest.fixture(autouse=True)
def isolate_naming_rules(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the deployment-matrix naming rules at the in-repo fixture.

    ``load_naming_rules`` defaults to ``D:\templateNaming\naming_rules.json``,
    an absolute path outside the repository. On any machine without that
    checkout the deployment-matrix routes answer 400 and their tests fail for a
    reason that has nothing to do with the code under test. The suite already
    ships ``fixtures/naming_rules.json`` for exactly this; make every web test
    use it rather than whatever happens to be on the operator's D: drive.

    Tests that pass ``path=`` explicitly are unaffected -- that argument wins.
    """
    fixture = Path(__file__).parent / "fixtures" / "naming_rules.json"
    monkeypatch.setenv("TA_NAMING_RULES_PATH", str(fixture))
    return fixture


@pytest.fixture(autouse=True)
def isolate_published_site(tmp_path: Path) -> Path:
    """Keep the published-site root out of the developer's real artifacts.

    ``/reports`` redirects to the published site when one exists. The real root
    lives under ``.ta_artifacts/web_optimizer/published-site``, so once anyone
    has published a weekly report on this machine the route 302s and every test
    expecting the rendered page fails. Restore afterwards so tests that set
    their own root are unaffected.
    """
    root = tmp_path / "_published_site"
    set_published_site_root(root)
    try:
        yield root
    finally:
        set_published_site_root(None)
