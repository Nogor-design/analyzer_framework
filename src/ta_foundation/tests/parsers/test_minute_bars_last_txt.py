from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import (
    MinuteBarsLastTxtParser,
)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_parse_accepts_minute_export_with_or_without_utf8_bom(
    tmp_path: Path,
    encoding: str,
) -> None:
    path = tmp_path / "NQ 09-26.Export.txt"
    path.write_text(
        "20260531 220100;30697.75;30723;30677.75;30707.25;714\n"
        "20260531 220200;30706.25;30712;30681.25;30683.25;416\n",
        encoding=encoding,
    )

    artifact = MinuteBarsLastTxtParser().parse(path, run_id=None)

    assert artifact.warnings == []
    assert artifact.summary == {
        "instrument": "NQ",
        "contract": "09-26",
        "source_tz": "UTC",
        "target_tz": "America/Denver",
        "n_rows": 2,
    }
    assert artifact.df is not None
    assert len(artifact.df) == 2
    assert artifact.df.iloc[0].to_dict() == {
        "dt": artifact.df.iloc[0]["dt"],
        "open": 30697.75,
        "high": 30723.0,
        "low": 30677.75,
        "close": 30707.25,
        "volume": 714,
    }
    assert artifact.df.iloc[0]["dt"].isoformat() == "2026-05-31T16:01:00-06:00"
