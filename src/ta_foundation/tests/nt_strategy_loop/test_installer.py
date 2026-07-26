from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.nt_strategy_loop.installer import (
    InstallerError,
    install_strategy_source,
    sha256_file,
)


def test_install_strategy_source_copies_to_strategies_folder(tmp_path: Path) -> None:
    source = tmp_path / "Source.cs"
    source.write_text("// payload", encoding="utf-8")
    nt_documents = tmp_path / "Documents"
    compile_root = tmp_path / "compile_root"

    installed = install_strategy_source(
        source,
        nt_documents_dir=nt_documents,
        compile_root=compile_root,
    )

    target = nt_documents / "bin" / "Custom" / "Strategies" / "Source.cs"
    staging = compile_root / "staging" / "Source.cs"
    assert target.is_file()
    assert staging.is_file()
    assert installed.sha256 == sha256_file(target)


def test_install_strategy_source_refuses_existing_target_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "Source.cs"
    source.write_text("// payload", encoding="utf-8")
    nt_documents = tmp_path / "Documents"
    target = nt_documents / "bin" / "Custom" / "Strategies" / "Source.cs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("// pre-existing", encoding="utf-8")

    with pytest.raises(InstallerError):
        install_strategy_source(
            source,
            nt_documents_dir=nt_documents,
            compile_root=tmp_path / "compile_root",
        )


def test_install_strategy_source_rejects_non_cs_files(tmp_path: Path) -> None:
    source = tmp_path / "Source.txt"
    source.write_text("// payload", encoding="utf-8")

    with pytest.raises(InstallerError):
        install_strategy_source(
            source,
            nt_documents_dir=tmp_path / "Documents",
            compile_root=tmp_path / "compile_root",
        )
