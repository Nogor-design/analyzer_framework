from pathlib import Path

from ta_foundation.optimization.result_intake import ingest_result_folder, write_intake_summary


def test_ingest_result_folder_accepts_keep_trades_file(tmp_path: Path):
    (tmp_path / "BotA_Summery.csv").write_text(
        """Performance,All trades,Long trades,Short trades
Start date,01/01/2026,,
Start time,12:00 AM,,
End date,01/10/2026,,
End time,11:59 PM,,
Total net profit,"$1,200.00",$800.00,$400.00
Profit factor,2.50,2.00,3.00
Max. drawdown,($300.00),($200.00),($100.00)
Total # of trades,6,4,2
Percent profitable,66.67%,75.00%,50.00%
Avg. trade,$200.00,$200.00,$200.00
""",
        encoding="utf-8",
    )
    (tmp_path / "BotA_Settings.csv").write_text(
        """Item,Value
Time,
StartTimeH,4
DurationTimeH,2
Test,
Reverse,true
averageFast,5
averageSlow,200
UseTrend,false
UseTrendReverse,false
MaxStop,100
MaxTPRatio,1.5
ProfitStop,1000
LossStop,800
MaxTrades,3
Long,true
Short,false
Bot_Name,Bot A
""",
        encoding="utf-8",
    )
    (tmp_path / "BotA_Trades_keep.csv").write_text(
        """Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,Entry name,Exit name,Profit,Cum. net profit,Commission,Clearing fee,Exchange fee,IP fee,NFA fee,MAE,MFE,ETD,Bars
1,NQ,Backtest,BotA,Long,1,1,2,01/01/2026 01:00,01/01/2026 01:10,E,X,$100.00,$100.00,$0,$0,$0,$0,$0,$10,$100,$0,1
2,NQ,Backtest,BotA,Long,1,1,2,01/02/2026 01:00,01/02/2026 01:10,E,X,$200.00,$300.00,$0,$0,$0,$0,$0,$10,$200,$0,1
3,NQ,Backtest,BotA,Long,1,1,2,01/03/2026 01:00,01/03/2026 01:10,E,X,$300.00,$600.00,$0,$0,$0,$0,$0,$10,$300,$0,1
4,NQ,Backtest,BotA,Long,1,1,2,01/04/2026 01:00,01/04/2026 01:10,E,X,$400.00,"$1,000.00",$0,$0,$0,$0,$0,$10,$400,$0,1
5,NQ,Backtest,BotA,Long,1,1,2,01/05/2026 01:00,01/05/2026 01:10,E,X,$500.00,"$1,500.00",$0,$0,$0,$0,$0,$10,$500,$0,1
6,NQ,Backtest,BotA,Long,1,1,2,01/06/2026 01:00,01/06/2026 01:10,E,X,$600.00,"$2,100.00",$0,$0,$0,$0,$0,$10,$600,$0,1
""",
        encoding="utf-8",
    )

    rows = ingest_result_folder(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row.run_id == "BotA"
    assert row.total_net_profit == 1200.0
    assert row.profit_factor == 2.5
    assert row.max_drawdown == -300.0
    assert row.trades == 6
    assert row.traded_days == 6
    assert row.percent_days_traded == 60.0
    assert row.last_5_trade_profit == 2000.0
    assert row.prior_5_trade_profit == 100.0
    assert row.recent_trade_delta == 1900.0
    assert row.start_hour == 4
    assert row.duration_hours == 2
    assert row.reverse == "true"
    assert row.average_fast == 5
    assert row.average_slow == 200
    assert row.use_trend == "false"
    assert row.use_trend_reverse == "false"
    assert row.max_stop == 100
    assert row.max_tp_ratio == 1.5
    assert row.profit_stop == 1000
    assert row.loss_stop == 800
    assert row.max_trades == 3
    assert row.long_enabled == "true"
    assert row.short_enabled == "false"
    assert row.bot_name == "Bot A"


def test_write_intake_summary_outputs_files(tmp_path: Path):
    (tmp_path / "OnlySummary_Summary.csv").write_text(
        """Performance,All trades,Long trades,Short trades
Total net profit,$100.00,,
Profit factor,1.50,,
Max. drawdown,($50.00),,
Total # of trades,2,,
""",
        encoding="utf-8",
    )

    output = tmp_path / "out"
    rows = write_intake_summary(tmp_path, output)

    assert len(rows) == 1
    assert (output / "result_intake.csv").exists()
    assert (output / "result_intake.json").exists()


def test_ingest_result_folder_ignores_batch_bookkeeping_csvs(tmp_path: Path):
    (tmp_path / "BatchRunSummary.csv").write_text("Name,Value\nA,1\n", encoding="utf-8")
    (tmp_path / "Executions.csv").write_text("Name,Value\nA,1\n", encoding="utf-8")
    (tmp_path / "RunA_Summary.csv").write_text(
        """Performance,All trades,Long trades,Short trades
Total net profit,$100.00,,
Profit factor,1.50,,
Max. drawdown,($50.00),,
Total # of trades,2,,
""",
        encoding="utf-8",
    )

    rows = ingest_result_folder(tmp_path)

    assert [row.run_id for row in rows] == ["RunA"]
