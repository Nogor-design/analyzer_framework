Below is the build plan I would use for your NinjaTrader 8 NY-open prediction engine.

This is structured to get you from raw session data → labeled dataset → trained model → daily pre-open bias output without overengineering it.

1. System objective

Build a model that answers this before the NY open:

Primary question

What is the most likely NY-open regime from 9:30 to 10:30 ET?

Recommended classes:

0 = trend_down

1 = trend_up

2 = chop

3 = sweep_reverse

This is better than simple up/down because it maps directly to execution logic.

2. Overall architecture

Use a 2-stage system.

Stage A — Feature generation

From NQ intraday data, calculate pre-9:30 features:

overnight range

overnight direction

VWAP position

SMA structure

prior day level relationships

volume profile relationships

volatility and calendar flags

Stage B — Model prediction

Train a classifier that predicts the NY-open regime.

Then use the output as a filter for your live strategies.

3. Recommended data sources

For each trading day, you want:

Required

1-minute NQ data

session timestamps

OHLCV

prior day OHLC

VWAP and VWAP bands

SMA 100 / 200

profile levels:

POC

VAH

VAL

Optional but useful later

cumulative delta

bid/ask imbalance

event calendar flags

opening drive stats

For the first version, do not wait for perfect order flow data.
Start with what you already have.

4. Trading session definitions

Use fixed session windows so the dataset is consistent.

Recommended session splits

Asia: 18:00–00:00 ET

London: 00:00–09:29 ET

NY Open label window: 09:30–10:30 ET

You can tune these later, but keep them fixed initially.

5. CSV schema

Use one row per trading day.

File name

nq_ny_open_features.csv

Core columns
Metadata

trade_date

day_of_week

month

is_month_end

is_quarter_end

Prior day levels

prior_high

prior_low

prior_close

prior_range

Current day open references

open_1800

open_0000

open_0930

price_0929

Overnight structure

asia_high

asia_low

asia_range

asia_close

asia_net_change

london_high

london_low

london_range

london_close

london_net_change

overnight_high

overnight_low

overnight_range

overnight_net_change

overnight_close_vs_open

overnight_close_vs_prior_close

VWAP structure at 09:29

vwap_0929

vwap_std1_up_0929

vwap_std1_dn_0929

vwap_std2_up_0929

vwap_std2_dn_0929

price_minus_vwap_0929

abs_price_minus_vwap_0929

vwap_band_position_0929

SMA structure at 09:29

sma100_0929

sma200_0929

price_minus_sma100_0929

price_minus_sma200_0929

sma100_slope_10bar

sma200_slope_10bar

sma_stack_state

Prior day relationships at 09:29

distance_to_prior_high_0929

distance_to_prior_low_0929

distance_to_prior_close_0929

inside_prior_range_0929

above_prior_high_0929

below_prior_low_0929

Volume profile features at 09:29

poc_overnight

vah_overnight

val_overnight

distance_to_poc_0929

distance_to_vah_0929

distance_to_val_0929

inside_value_area_0929

above_value_area_0929

below_value_area_0929

value_area_width

value_area_mid

Volatility / participation

atr14_5m_0929

atr14_15m_0929

overnight_volume

london_volume

overnight_range_vs_prior_range

overnight_range_vs_atr

News / event flags

is_cpi_day

is_ppi_day

is_nfp_day

is_fomc_day

is_major_event_day

Label columns

label_regime

label_direction

mfe_up_0930_1030

mfe_down_0930_1030

close_change_0930_1030

6. Feature engineering rules

These should be deterministic and consistent.

A. Overnight direction score

A simple version:

overnight_direction_score = (
    np.sign(overnight_net_change) *
    min(abs(overnight_net_change) / max(overnight_range, 1), 1.0)
)

This gives a normalized directional measure.

B. VWAP band position

Encode the location of price relative to VWAP bands:

-2 = below -2σ

-1 = between -1σ and -2σ

0 = between -1σ and +1σ

1 = between +1σ and +2σ

2 = above +2σ

This is very useful for classification.

C. SMA stack state

Encode structure cleanly:

0 = bullish_stack → price > sma100 > sma200

1 = bearish_stack → price < sma100 < sma200

2 = mixed

3 = compression

For the first version, keep it this simple.

D. Prior range state

Encode where price is at 09:29:

0 = inside_prior_range

1 = above_prior_high

2 = below_prior_low

Very useful for open behavior.

E. Profile state

At 09:29:

0 = inside_value

1 = above_value

2 = below_value

Again, simple and high value.

7. Label rules

This matters a lot. Use label rules that match how you trade.

Label window

Use:

Start: 09:30:00 ET

End: 10:30:00 ET

Measurements

From the 9:30 opening price, calculate:

mfe_up_0930_1030 = highest price in window - open price

mfe_down_0930_1030 = open price - lowest price in window

close_change_0930_1030 = 10:30 price - 9:30 open

Recommended regime labeling
trend_up

Assign trend_up if:

mfe_up >= 40

mfe_down <= 20

close_change > 20

trend_down

Assign trend_down if:

mfe_down >= 40

mfe_up <= 20

close_change < -20

sweep_reverse

Assign sweep_reverse if:

both mfe_up >= 30 and mfe_down >= 30

This captures two-sided expansion.

chop

Assign chop if none of the above.

Numeric encoding

0 = trend_down

1 = trend_up

2 = chop

3 = sweep_reverse

Also create a binary direction label:

0 = bearish

1 = bullish

For binary direction:

bullish if close_change > 20

bearish if close_change < -20

otherwise drop or classify neutral separately

8. Recommended folder structure

Use this structure:

ny_open_model/
├── data/
│   ├── raw/
│   │   ├── nq_1m.csv
│   │   ├── prior_day_levels.csv
│   │   ├── vwap_levels.csv
│   │   └── overnight_profile_levels.csv
│   ├── processed/
│   │   ├── nq_ny_open_features.csv
│   │   └── nq_ny_open_train.csv
├── models/
│   ├── ny_open_regime_xgb.pkl
│   ├── ny_open_direction_logreg.pkl
│   └── feature_columns.json
├── notebooks/
│   └── exploration.ipynb
├── src/
│   ├── build_features.py
│   ├── build_labels.py
│   ├── train_model.py
│   ├── evaluate_model.py
│   └── predict_today.py
└── reports/
    ├── feature_importance.csv
    ├── confusion_matrix.png
    └── model_metrics.json
9. Training approach

Start with three models.

Baseline 1

Logistic Regression

Purpose:

sanity check

interpretability

Baseline 2

Random Forest

Purpose:

nonlinear baseline

Main model

XGBoost or LightGBM

Purpose:

strongest first production candidate

For your use case, XGBoost is the best first main model.

10. Data split rules

Do not randomly shuffle all days.
Use a chronological split.

Recommended split

Train: oldest 70%

Validation: next 15%

Test: latest 15%

This avoids leakage and better reflects live trading conditions.

11. Metrics to track

Do not use only accuracy.

Track:

overall accuracy

macro F1

confusion matrix

precision for trend_down

precision for trend_up

accuracy on high-volatility days

profit impact when used as a filter

The last one matters most.

12. How to use the model in trading

The model should not place trades directly at first.

Use it as a pre-open strategy gate.

Example deployment logic
If prediction = trend_down

enable short breakout and short pullback systems

disable long continuation systems

reduce countertrend longs

If prediction = trend_up

enable long breakout and long pullback systems

disable short continuation systems

If prediction = chop

disable breakout systems

enable mean-reversion only, or stand down

If prediction = sweep_reverse

wait for initial open sweep

do not chase first impulse

enable reversal logic only after confirmation

That is where the real value is.

13. Python training script structure

Below is the exact script layout I recommend.

build_features.py

Purpose:

read raw minute data

compute one row per day

export feature CSV

build_labels.py

Purpose:

calculate 9:30–10:30 label values

merge with features

train_model.py

Purpose:

split chronologically

train models

save best model

evaluate_model.py

Purpose:

create confusion matrix

feature importance

summary metrics

predict_today.py

Purpose:

read latest pre-market data

compute current day features

output regime prediction and confidence

14. Example minimum viable Python code

Below is the starter structure. This is not your final production version, but it is the right skeleton.

import json
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.metrics import classification_report, accuracy_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

DATA_PATH = Path("data/processed/nq_ny_open_train.csv")
MODEL_PATH = Path("models/ny_open_regime_xgb.pkl")
FEATURES_PATH = Path("models/feature_columns.json")


def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    exclude = {
        "trade_date",
        "label_regime",
        "label_direction",
        "mfe_up_0930_1030",
        "mfe_down_0930_1030",
        "close_change_0930_1030",
    }
    return [c for c in df.columns if c not in exclude]


def chronological_split(df: pd.DataFrame, train_pct: float = 0.70, val_pct: float = 0.15):
    n = len(df)
    train_end = int(n * train_pct)
    val_end = int(n * (train_pct + val_pct))

    train_df = df.iloc[:train_end].copy()
    val_df = df.iloc[train_end:val_end].copy()
    test_df = df.iloc[val_end:].copy()

    return train_df, val_df, test_df


def train_xgb(train_df: pd.DataFrame, val_df: pd.DataFrame, feature_cols: list[str]):
    x_train = train_df[feature_cols]
    y_train = train_df["label_regime"]

    x_val = val_df[feature_cols]
    y_val = val_df["label_regime"]

    model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="multi:softprob",
        num_class=4,
        eval_metric="mlogloss",
        random_state=42,
    )

    model.fit(
        x_train,
        y_train,
        eval_set=[(x_val, y_val)],
        verbose=False,
    )
    return model


def evaluate(model, df: pd.DataFrame, feature_cols: list[str], label_col: str, name: str):
    x = df[feature_cols]
    y = df[label_col]
    preds = model.predict(x)

    print(f"\n{name} accuracy: {accuracy_score(y, preds):.4f}")
    print(classification_report(y, preds, digits=4))


def main():
    df = load_data(DATA_PATH)

    # Drop rows with missing target or feature values
    df = df.dropna(subset=["label_regime"]).copy()
    feature_cols = get_feature_columns(df)
    df = df.dropna(subset=feature_cols).copy()

    train_df, val_df, test_df = chronological_split(df)

    model = train_xgb(train_df, val_df, feature_cols)

    evaluate(model, train_df, feature_cols, "label_regime", "Train")
    evaluate(model, val_df, feature_cols, "label_regime", "Validation")
    evaluate(model, test_df, feature_cols, "label_regime", "Test")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)

    with open(FEATURES_PATH, "w", encoding="utf-8") as f:
        json.dump(feature_cols, f, indent=2)

    print(f"\nSaved model to {MODEL_PATH}")
    print(f"Saved feature columns to {FEATURES_PATH}")


if __name__ == "__main__":
    main()
15. Example feature builder logic

Below is the pattern, not the full finished script.

def compute_vwap_band_position(price, vwap, std1_up, std1_dn, std2_up, std2_dn):
    if price < std2_dn:
        return -2
    if price < std1_dn:
        return -1
    if price <= std1_up:
        return 0
    if price <= std2_up:
        return 1
    return 2


def compute_sma_stack_state(price, sma100, sma200, compression_threshold=8.0):
    if abs(sma100 - sma200) <= compression_threshold:
        return 3
    if price > sma100 > sma200:
        return 0
    if price < sma100 < sma200:
        return 1
    return 2


def compute_profile_state(price, vah, val):
    if price > vah:
        return 1
    if price < val:
        return 2
    return 0
16. Example live prediction output

Your predict_today.py should return something like:

Date: 2026-03-06
Prediction: trend_down
Confidence: 0.68

Secondary probabilities:
trend_down: 0.68
trend_up: 0.08
chop: 0.14
sweep_reverse: 0.10

Suggested bias:
Allow short pullback and short continuation setups only.
Avoid aggressive countertrend longs in first hour.

That is the format your automation layer can consume.

17. NinjaTrader integration path

For now, keep the ML outside NinjaTrader.

Best setup

NinjaTrader exports the pre-open data

Python computes features and prediction

Python writes a small output file:

CSV

JSON

TXT

Then NinjaTrader strategy reads that file before the open.

Example JSON output
{
  "trade_date": "2026-03-06",
  "prediction": "trend_down",
  "confidence": 0.68,
  "allow_long": false,
  "allow_short": true,
  "allow_mean_reversion": false
}

That is the cleanest first deployment path.

18. Version 1 build order

Do this in order.

Phase 1

Build dataset using only:

overnight range

overnight net change

price vs VWAP

price vs SMA200

prior day level distances

profile distances to POC / VAH / VAL

Phase 2

Train binary direction model

Phase 3

Upgrade to 4-class regime model

Phase 4

Add major event-day flags

Phase 5

Add order-flow/delta features

That will keep the project controlled.

19. What I would do first in your shoes

This is the exact next move:

Step 1

Create a daily-row CSV schema exactly like above.

Step 2

Export at least 6–12 months of NQ data.

Step 3

Build the first dataset with:

price

VWAP

prior day levels

SMA100 / SMA200

profile levels

Step 4

Train a simple binary model first.

Step 5

Then expand to regime classification.

That is the most efficient path.

20. Final recommendation

Your best predictive stack is now:

VWAP / prior day levels

Volume profile

SMA trend structure

That is enough to build a useful NY-open classifier.

The right first deliverable is not RL and not a fully autonomous bot.
It is a pre-open regime filter that tells your systems what kind of day they are likely walking into.

Next step for the AI team:
Go to the Builder / Quant Dev role and say:

“Build me a Python project that creates one row per day for NQ, using pre-9:30 features from VWAP, prior day OHLC, SMA100/200, and overnight profile levels, then trains an XGBoost classifier to predict the 9:30–10:30 NY-open regime.”