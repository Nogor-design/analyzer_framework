from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_plan import build_plan_preview
from ta_foundation.web.optimizer_template_writer import (
    TemplateWriteError,
    generate_session_templates,
)


SEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.FakeStrategy</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizerParameters>
    <ArrayOfParameterWrapper xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <ParameterWrapper>
        <Name>KeepBestResults</Name>
        <Value xsi:type="xsd:int">500</Value>
      </ParameterWrapper>
    </ArrayOfParameterWrapper>
  </OptimizerParameters>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <Strategy>
    <FakeStrategy>
      <averageSlow>100</averageSlow>
      <MaxStop>50</MaxStop>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
  <OptimizationParameters>
    <ArrayOfParameter xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Parameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">100</Max>
        <Min xsi:type="xsd:int">100</Min>
        <Name>averageSlow</Name>
        <ValueSerializable>100</ValueSerializable>
      </Parameter>
      <Parameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">50</Max>
        <Min xsi:type="xsd:int">50</Min>
        <Name>MaxStop</Name>
        <ValueSerializable>50</ValueSerializable>
      </Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
</StrategyTemplate>
"""


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


@pytest.fixture
def session_with_plan(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    session.update(
        parameters=[
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 200, "increment": 50},
            {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
        ],
        chunking={"max_combinations_per_chunk": 2, "keep_best_results": 750},
    )
    plan = build_plan_preview(session.load_document())
    session.save_plan(plan.to_dict())
    return session, plan


def test_generate_writes_one_xml_per_chunk(session_with_plan):
    session, plan = session_with_plan
    written = generate_session_templates(session)
    assert len(written) == len(plan.chunks)
    output_dir = session.directory / "generated_templates"
    files = sorted(output_dir.glob("*.xml"))
    assert {f.name for f in files} == {f"{c.chunk_id}.xml" for c in plan.chunks}


def test_generate_patches_optimization_parameter_ranges(session_with_plan):
    session, plan = session_with_plan
    written = generate_session_templates(session)
    # First chunk should carry a slice of averageSlow.
    first_xml = Path(written[0].path).read_text(encoding="utf-8")
    # The slice's min should NOT be 100 (the seed value); should be 50.
    assert "<Min xsi:type=\"xsd:int\">50</Min>" in first_xml
    # Fixed MaxStop pinned to 100 (Min == Max).
    assert first_xml.count("<Min xsi:type=\"xsd:int\">100</Min>") >= 1
    assert first_xml.count("<Max xsi:type=\"xsd:int\">100</Max>") >= 1


def test_generate_patches_strategy_value_for_fixed_parameter(session_with_plan):
    session, _ = session_with_plan
    written = generate_session_templates(session)
    xml = Path(written[0].path).read_text(encoding="utf-8")
    # Fixed MaxStop value should appear inside the <Strategy> block (100, not the seed's 50).
    assert "<MaxStop>100</MaxStop>" in xml


def test_generate_optionally_overrides_optimizer_type(session_with_plan):
    session, _ = session_with_plan
    written = generate_session_templates(
        session,
        optimizer_type="NinjaTrader.NinjaScript.Optimizers.CustomMultiObjectiveOptimizer",
        optimization_fitness="NinjaTrader.NinjaScript.OptimizationFitnesses.CustomMultiObjectiveFitness",
    )
    xml = Path(written[0].path).read_text(encoding="utf-8")
    assert "CustomMultiObjectiveOptimizer" in xml
    assert "CustomMultiObjectiveFitness" in xml


def test_generate_patches_keep_best_results(session_with_plan):
    session, _ = session_with_plan
    written = generate_session_templates(session)
    xml = Path(written[0].path).read_text(encoding="utf-8")
    assert "<Name>KeepBestResults</Name>" in xml
    assert "<Value xsi:type=\"xsd:int\">750</Value>" in xml


def test_generate_preserves_full_seed_contract_when_session_has_generic_root(session_with_plan):
    session, _ = session_with_plan
    written = generate_session_templates(session)

    for item in written:
        xml = Path(item.path).read_text(encoding="utf-8")
        assert "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>" in xml
        assert "<InstrumentOrInstrumentList>NQ</InstrumentOrInstrumentList>" not in xml


def test_generate_patches_generic_seed_contract_from_full_session_instrument(session_with_plan):
    session, _ = session_with_plan
    doc = session.load_document()
    seed_path = Path(doc.seed_template_path)
    seed_path.write_text(
        SEED_XML.replace(
            "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>",
            "<InstrumentOrInstrumentList>NQ</InstrumentOrInstrumentList>",
        ),
        encoding="utf-8",
    )
    session.update(instrument="NQ 06-26")

    written = generate_session_templates(session)

    for item in written:
        xml = Path(item.path).read_text(encoding="utf-8")
        assert "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>" in xml
        assert "<InstrumentOrInstrumentList>NQ</InstrumentOrInstrumentList>" not in xml


def test_generate_errors_when_no_plan(tmp_path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy", seed_template_path=str(seed_path)
    )
    with pytest.raises(TemplateWriteError):
        generate_session_templates(session)


def test_generate_errors_when_seed_missing(tmp_path):
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "does_not_exist.xml"),
    )
    session.save_plan({"chunks": [{"chunk_id": "chunk_001", "optimized": [], "fixed": []}]})
    with pytest.raises(TemplateWriteError):
        generate_session_templates(session)


def test_bool_sweep_emits_integer_increment(tmp_path):
    """Regression: a sweep on a bool parameter must write Increment=1, not
    Increment=true. NT collapses the sweep to a single value when Increment
    is a bool, which is what caused our first live regression run to only
    test Reverse=False."""
    seed_xml = SEED_XML.replace(
        "<Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:int\">100</Max>\n        <Min xsi:type=\"xsd:int\">100</Min>\n        <Name>averageSlow</Name>\n        <ValueSerializable>100</ValueSerializable>\n      </Parameter>",
        "<Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:int\">100</Max>\n        <Min xsi:type=\"xsd:int\">100</Min>\n        <Name>averageSlow</Name>\n        <ValueSerializable>100</ValueSerializable>\n      </Parameter>\n      <Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:boolean\">true</Max>\n        <Min xsi:type=\"xsd:boolean\">false</Min>\n        <Name>Reverse</Name>\n        <ValueSerializable>false</ValueSerializable>\n      </Parameter>",
    )
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(seed_xml, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy", seed_template_path=str(seed_path), instrument="NQ 06-26"
    )
    session.update(parameters=[
        {"name": "Reverse", "type_name": "bool", "mode": "optimize",
         "minimum": False, "maximum": True, "increment": 1},
    ])
    session.save_plan({
        "chunks": [
            {
                "chunk_id": "chunk_001",
                "optimized": [
                    {"name": "Reverse", "type_name": "bool",
                     "minimum": False, "maximum": True, "increment": 1, "step_count": 2},
                ],
                "fixed": [],
            }
        ],
        "combination_estimate": 2,
    })
    written = generate_session_templates(session)
    xml = Path(written[0].path).read_text(encoding="utf-8")
    # Locate the Reverse <Parameter> block and check Increment is "1".
    import re
    m = re.search(
        r"<Parameter>(?:(?!</Parameter>).)*<Name>Reverse</Name>(?:(?!</Parameter>).)*</Parameter>",
        xml,
        re.DOTALL,
    )
    assert m is not None, "Reverse Parameter block missing"
    block = m.group(0)
    inc_match = re.search(r"<Increment>(.*?)</Increment>", block)
    assert inc_match is not None
    assert inc_match.group(1) == "1", f"expected Increment=1 for bool sweep, got {inc_match.group(1)!r}"
    # Min/Max should still be the bool string forms.
    assert "<Min xsi:type=\"xsd:boolean\">false</Min>" in block
    assert "<Max xsi:type=\"xsd:boolean\">true</Max>" in block


def test_fixed_bool_patches_strategy_value_and_pins_optimization_block(tmp_path):
    """Audit: a fixed bool parameter must (a) flip the <Strategy> tag value
    and (b) pin the <OptimizationParameters> Min/Max to the same bool with
    Increment=1, so NT does not silently sweep it."""
    seed_xml = SEED_XML.replace(
        "<Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:int\">50</Max>\n        <Min xsi:type=\"xsd:int\">50</Min>\n        <Name>MaxStop</Name>\n        <ValueSerializable>50</ValueSerializable>\n      </Parameter>",
        "<Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:int\">50</Max>\n        <Min xsi:type=\"xsd:int\">50</Min>\n        <Name>MaxStop</Name>\n        <ValueSerializable>50</ValueSerializable>\n      </Parameter>\n      <Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:boolean\">true</Max>\n        <Min xsi:type=\"xsd:boolean\">false</Min>\n        <Name>Reverse</Name>\n        <ValueSerializable>false</ValueSerializable>\n      </Parameter>",
    ).replace(
        "<MaxStop>50</MaxStop>",
        "<MaxStop>50</MaxStop>\n      <Reverse>false</Reverse>",
    )
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(seed_xml, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy", seed_template_path=str(seed_path), instrument="NQ 06-26"
    )
    session.save_plan({
        "chunks": [
            {
                "chunk_id": "chunk_001",
                "optimized": [],
                "fixed": [
                    {"name": "Reverse", "type_name": "bool", "value": True},
                ],
            }
        ],
        "combination_estimate": 1,
    })
    written = generate_session_templates(session)
    xml = Path(written[0].path).read_text(encoding="utf-8")
    # Strategy block must show the new value (lowercase per NT convention).
    assert "<Reverse>true</Reverse>" in xml
    # OptimizationParameters block must pin Min=Max=true and Increment=1.
    import re
    m = re.search(
        r"<Parameter>(?:(?!</Parameter>).)*<Name>Reverse</Name>(?:(?!</Parameter>).)*</Parameter>",
        xml, re.DOTALL,
    )
    assert m, "Reverse Parameter block missing"
    block = m.group(0)
    assert "<Min xsi:type=\"xsd:boolean\">true</Min>" in block
    assert "<Max xsi:type=\"xsd:boolean\">true</Max>" in block
    assert re.search(r"<Increment>1</Increment>", block)


def test_double_sweep_emits_float_increment(tmp_path):
    """Audit: a double parameter sweep must keep the float increment as
    `0.5`, not collapse it to int."""
    seed_xml = SEED_XML.replace(
        "<Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:int\">50</Max>\n        <Min xsi:type=\"xsd:int\">50</Min>\n        <Name>MaxStop</Name>\n        <ValueSerializable>50</ValueSerializable>\n      </Parameter>",
        "<Parameter>\n        <Increment>1</Increment>\n        <Max xsi:type=\"xsd:int\">50</Max>\n        <Min xsi:type=\"xsd:int\">50</Min>\n        <Name>MaxStop</Name>\n        <ValueSerializable>50</ValueSerializable>\n      </Parameter>\n      <Parameter>\n        <Increment>0.1</Increment>\n        <Max xsi:type=\"xsd:double\">2</Max>\n        <Min xsi:type=\"xsd:double\">0.5</Min>\n        <Name>MaxTPRatio</Name>\n        <ValueSerializable>1</ValueSerializable>\n      </Parameter>",
    )
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(seed_xml, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy", seed_template_path=str(seed_path), instrument="NQ 06-26"
    )
    session.save_plan({
        "chunks": [
            {
                "chunk_id": "chunk_001",
                "optimized": [
                    {"name": "MaxTPRatio", "type_name": "double",
                     "minimum": 0.5, "maximum": 2.0, "increment": 0.5, "step_count": 4},
                ],
                "fixed": [],
            }
        ],
        "combination_estimate": 4,
    })
    written = generate_session_templates(session)
    xml = Path(written[0].path).read_text(encoding="utf-8")
    import re
    m = re.search(
        r"<Parameter>(?:(?!</Parameter>).)*<Name>MaxTPRatio</Name>(?:(?!</Parameter>).)*</Parameter>",
        xml, re.DOTALL,
    )
    assert m
    block = m.group(0)
    assert "<Min xsi:type=\"xsd:double\">0.5</Min>" in block
    assert "<Max xsi:type=\"xsd:double\">2</Max>" in block
    inc = re.search(r"<Increment>(.*?)</Increment>", block)
    assert inc and inc.group(1) == "0.5", f"increment should be 0.5, got {inc.group(1) if inc else None!r}"


def test_generate_removes_stale_chunks_from_previous_run(session_with_plan):
    session, _ = session_with_plan
    output_dir = session.directory / "generated_templates"
    output_dir.mkdir(parents=True, exist_ok=True)
    stale = output_dir / "chunk_zzz_stale.xml"
    stale.write_text("<stale/>", encoding="utf-8")
    generate_session_templates(session)
    assert not stale.exists()
