from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web.optimizer_namer import NamerError, run_template_namer


def test_run_template_namer_invokes_runner_and_collects_outputs(tmp_path: Path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"
    naming_dir = tmp_path / "templateNaming"
    naming_dir.mkdir()

    invoked = {}

    def fake_runner(cmd, cwd, timeout):
        invoked["cmd"] = list(cmd)
        invoked["cwd"] = cwd
        invoked["timeout"] = timeout
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "RisingApolloBoltB-NQ.xml").write_text("<r/>", encoding="utf-8")
        return 0, "renamed 1 file\n", ""

    result = run_template_namer(
        input_dir=in_dir,
        output_dir=out_dir,
        market="NQ",
        template_naming_dir=naming_dir,
        runner=fake_runner,
    )
    assert result.returncode == 0
    assert result.output_files == [str(out_dir / "RisingApolloBoltB-NQ.xml")]
    assert "--market" in invoked["cmd"]
    assert invoked["cwd"] == naming_dir


def test_run_template_namer_errors_for_missing_input(tmp_path: Path):
    naming_dir = tmp_path / "templateNaming"
    naming_dir.mkdir()
    with pytest.raises(NamerError):
        run_template_namer(
            input_dir=tmp_path / "missing",
            output_dir=tmp_path / "out",
            template_naming_dir=naming_dir,
            runner=lambda *a, **k: (0, "", ""),
        )


def test_run_template_namer_errors_when_namer_unavailable(tmp_path: Path, monkeypatch):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    monkeypatch.setattr("ta_foundation.web.optimizer_namer.shutil.which", lambda _: None)
    with pytest.raises(NamerError):
        run_template_namer(
            input_dir=in_dir,
            output_dir=tmp_path / "out",
            template_naming_dir=tmp_path / "definitely_missing",
            runner=lambda *a, **k: (0, "", ""),
        )
