You are working inside my existing market prediction system.



I have uploaded or provided the current codebase. Start by reading the prediction system, especially:



\- statistical\_stub\_agent

\- orchestrator.py

\- models.py

\- context\_builder.py

\- scorer.py

\- calibrator.py

\- outcome\_measurer.py

\- store.py

\- run\_prediction.py

\- prediction.yaml / config handling if present



Current purpose:

The system currently predicts the next trading session using a Claude agent and has a statistical\_stub\_agent used for dry runs. The statistical stub is currently too simple. I want to turn it into a robust statistical probability engine that can act as a serious baseline and eventually as one member of a multi-agent prediction team.



Important design goal:

Do not merely make the stub “smarter” with arbitrary rules. Design it around statistically sound, testable, calibrated probability forecasting.



Main expansion:

The prediction system should support next-N-candle forecasting across multiple timeframes:



\- Daily candles: predict the next 1 to N daily candles

\- 4h candles: predict the next 1 to N 4h candles

\- 1h candles: predict the next 1 to N 1h candles

\- 30m candles: predict the next 1 to N 30m candles

\- 15m candles: predict the next 1 to N 15m candles

\- 5m candles: predict the next 1 to N 5m candles



It should also rank performance by market/session window:



\- Asia

\- London

\- New York

\- New York open

\- New York midday

\- New York close



The goal is to discover where each agent and each statistical method is actually useful. For example, maybe the statistical engine is weak on daily predictions but strong on London 5m next-3-candle direction, or maybe Claude is better at daily context while statistical analogues are better at short-horizon continuation/reversal probabilities.



What I want you to do:



1\. Inspect the current architecture.

2\. Identify how the current statistical\_stub\_agent works.

3\. Propose a robust design for a true statistical probability agent.

4\. Expand the prediction model so it can support multi-timeframe, multi-horizon forecasts.

5\. Design the data structures needed to store forecasts and outcomes for each:

&#x20;  - instrument

&#x20;  - contract

&#x20;  - timeframe

&#x20;  - as-of timestamp

&#x20;  - session window

&#x20;  - forecast horizon

&#x20;  - agent\_id

6\. Design the statistical methods that should be implemented.

7\. Design scoring and calibration for next-N-candle predictions.

8\. Design ranking reports by timeframe, horizon, session, regime, and agent.

9\. Then create a phased implementation plan.

10\. If safe, begin implementing Phase 1 only, keeping changes small and testable.



Statistical prediction methods to consider:



A. Historical conditional frequency model



For a given setup/context, estimate:



\- probability next N candles close up

\- probability next N candles close down

\- probability next N candles are neutral/choppy

\- expected return over N candles

\- expected max favorable excursion

\- expected max adverse excursion

\- probability of reaching an upside threshold

\- probability of reaching a downside threshold

\- probability of inside movement / no meaningful move



This should be conditioned on features such as:



\- timeframe

\- session window

\- time of day

\- day of week

\- volatility regime

\- ATR percentile

\- trend regime

\- distance from moving average

\- prior candle body size

\- prior candle direction

\- wick/body ratio

\- volume regime if available

\- prior N-candle momentum

\- range expansion/contraction

\- proximity to prior day high/low/close

\- proximity to session open/high/low

\- economic event proximity if available



B. Similarity / analogue model



Improve the current historical analogue approach.



Use normalized feature vectors and nearest neighbors, but make it more rigorous:



\- configurable K nearest neighbors

\- distance weighting

\- minimum sample thresholds

\- fallback hierarchy when too few analogues exist

\- regime-aware analogue search

\- session-aware analogue search

\- timeframe-aware analogue search



The output should not be a single prediction. It should be a probability distribution.



C. Bayesian smoothing



Use Bayesian or empirical-Bayes smoothing so small samples do not create overconfident predictions.



For example:

\- If only 5 similar samples exist and 4 were bullish, do not output 80% bullish confidence blindly.

\- Shrink the probability toward the broader baseline for that timeframe/session/horizon.

\- Use priors from broader groups:

&#x20; 1. instrument-level baseline

&#x20; 2. timeframe baseline

&#x20; 3. timeframe + session baseline

&#x20; 4. timeframe + session + regime baseline

&#x20; 5. nearest-neighbor local estimate



D. Logistic or simple ML baseline



Consider a simple, interpretable model before deep learning:



\- logistic regression for direction probability

\- quantile regression or distribution estimates for return/range

\- random forest or gradient boosting only if the architecture supports it cleanly

\- walk-forward validation only

\- no leakage from future candles



E. Markov / transition model



Consider modeling candle-state transitions:



Current state examples:

\- strong bullish candle

\- strong bearish candle

\- small doji/chop candle

\- breakout candle

\- reversal wick candle

\- compression candle

\- range expansion candle



Then estimate:

\- probability of continuation

\- probability of reversal

\- probability of compression

\- probability of expansion

over the next N candles.



F. Distribution forecast, not just direction



For each horizon, output:



\- direction probabilities:

&#x20; - bullish probability

&#x20; - bearish probability

&#x20; - neutral probability



\- return distribution:

&#x20; - expected return

&#x20; - median return

&#x20; - p10 return

&#x20; - p25 return

&#x20; - p75 return

&#x20; - p90 return



\- path statistics:

&#x20; - expected MFE

&#x20; - expected MAE

&#x20; - probability upside threshold hit first

&#x20; - probability downside threshold hit first

&#x20; - probability neither threshold hit

&#x20; - expected efficiency ratio



This is important because a trading strategy needs more than direction. It needs to know whether the expected move is large enough relative to stop, target, spread, slippage, and drawdown constraints.



Prediction object design:



Create or propose a new model such as:



CandleHorizonPrediction:

\- prediction\_id

\- agent\_id

\- instrument

\- contract

\- timeframe

\- asof\_timestamp

\- session\_label

\- horizon\_candles

\- bullish\_probability

\- bearish\_probability

\- neutral\_probability

\- confidence

\- expected\_return\_points

\- expected\_return\_atr

\- median\_return\_points

\- p10\_return\_points

\- p90\_return\_points

\- expected\_mfe\_points

\- expected\_mae\_points

\- upside\_threshold\_probability

\- downside\_threshold\_probability

\- neither\_threshold\_probability

\- predicted\_volatility

\- sample\_size

\- effective\_sample\_size

\- method\_used

\- fallback\_level

\- calibration\_bucket

\- feature\_snapshot

\- reasoning\_summary



Outcome object design:



CandleHorizonOutcome:

\- prediction\_id

\- actual\_return\_points

\- actual\_return\_atr

\- actual\_direction

\- actual\_mfe\_points

\- actual\_mae\_points

\- upside\_threshold\_hit

\- downside\_threshold\_hit

\- threshold\_hit\_order

\- actual\_efficiency\_ratio

\- brier\_score\_direction

\- log\_loss\_direction

\- calibration\_error

\- return\_error

\- mfe\_error

\- mae\_error

\- composite\_score



Scoring:



Use proper scoring rules.



Direction:

\- Brier score for bullish/bearish/neutral probability vector

\- optionally log loss, but protect against zero probabilities



Return:

\- MAE between predicted expected return and actual return

\- pinball loss for quantile forecasts if quantiles are produced



Threshold probabilities:

\- Brier score for upside hit

\- Brier score for downside hit

\- Brier score for neither hit



Path:

\- error for predicted MFE and MAE

\- efficiency ratio error



Composite:

Create a configurable composite score, for example:

\- 35% direction probability score

\- 25% threshold probability score

\- 20% return distribution score

\- 10% MFE/MAE path score

\- 10% calibration score



But make this configurable in prediction.yaml.



Session ranking:



The reporting system should rank predictions by:



\- agent\_id

\- timeframe

\- horizon\_candles

\- session\_label

\- market regime

\- volatility regime

\- sample size

\- rolling score

\- calibration error

\- win rate when confidence > threshold

\- average predicted edge

\- average realized edge

\- overconfidence penalty



Reports I want:



1\. Agent Leaderboard

Shows which agents are best overall and by condition.



2\. Timeframe × Horizon Matrix

Rows: timeframe

Columns: horizon

Values:

\- composite score

\- Brier score

\- sample count

\- calibration error



3\. Session Performance Matrix

Rows: Asia, London, NY, NY open, NY midday, NY close

Columns:

\- 5m next 1

\- 5m next 3

\- 5m next 5

\- 15m next 1

\- 15m next 3

\- 1h next 1

\- daily next 1



4\. Best Edge Finder

Find combinations where the model has historically performed best:

\- timeframe

\- session

\- horizon

\- regime

\- direction

\- confidence bucket

\- minimum sample size



5\. Calibration Report

For each agent/timeframe/session/horizon:

\- when it says 55–60%, how often is it right?

\- 60–65%

\- 65–70%

\- 70–75%

\- etc.



6\. Drift Report

Detect when recent prediction quality has degraded versus longer-term performance.



Important anti-leakage rules:



\- Never use future candles to build features for a prediction.

\- For each as-of timestamp, only use bars available before or at that timestamp.

\- Historical analogue search must only include examples strictly before the prediction timestamp when doing walk-forward tests.

\- Session labels must be computed using timestamp only, not future behavior.

\- Outcomes must be measured separately after the horizon completes.

\- Backtests must be walk-forward, not random train/test split.



Session definitions:



Propose defaults, but make them configurable.



Use America/New\_York and/or America/Denver carefully. The code should be explicit about timezone handling.



Suggested session buckets:

\- Asia

\- London

\- NY open

\- NY midday

\- NY close

\- Full NY

\- Overnight



The exact times should be config-driven because futures markets and DST make hardcoding dangerous.



Architecture preference:



Do not create a huge tangled implementation.



Prefer modular files such as:



\- horizon\_models.py

\- horizon\_context\_builder.py

\- statistical\_probability\_agent.py

\- horizon\_outcome\_measurer.py

\- horizon\_scorer.py

\- session\_classifier.py

\- horizon\_reports.py

\- backtest\_horizon\_predictions.py



Or propose better names if they fit the current codebase.



Implementation phases:



Phase 1:

\- Add data models for CandleHorizonPrediction and CandleHorizonOutcome.

\- Add session classifier.

\- Add horizon outcome measurement.

\- Add simple baseline probability model using historical conditional frequencies.

\- Add tests.



Phase 2:

\- Add nearest-neighbor analogue model with distance weighting.

\- Add Bayesian smoothing and fallback hierarchy.

\- Add calibration tracking.



Phase 3:

\- Add multi-timeframe batch prediction.

\- Add walk-forward backtesting.

\- Add ranking reports.



Phase 4:

\- Add model ensemble logic:

&#x20; - statistical probability agent

&#x20; - Claude reasoning agent

&#x20; - regime agent

&#x20; - level agent

&#x20; - volatility agent

&#x20; - session specialist agents



Phase 5:

\- Add strategy-useful outputs:

&#x20; - only show predictions with positive expected edge

&#x20; - identify where the model should abstain

&#x20; - output recommended “tradable forecast zones”

&#x20; - rank best agent/timeframe/session/horizon combinations



Abstention logic:



The system should be allowed to say:

\- no edge

\- insufficient sample size

\- uncalibrated

\- regime drift detected

\- confidence too low



This is important. A useful prediction system should not force predictions everywhere.



Expected final output from you:



First produce a design document with:



1\. Current architecture summary

2\. Problems with current statistical\_stub\_agent

3\. Proposed prediction framework

4\. Data model changes

5\. Statistical methods

6\. Scoring and calibration

7\. Session/timeframe/horizon ranking

8\. Anti-leakage safeguards

9\. Implementation plan

10\. File-by-file change plan



Then implement Phase 1 only unless the codebase is already structured enough to safely implement more.



When implementing:

\- keep changes small

\- preserve existing daily prediction behavior

\- avoid breaking run\_prediction.py

\- add tests or example scripts where appropriate

\- make defaults configurable

\- document assumptions clearly

