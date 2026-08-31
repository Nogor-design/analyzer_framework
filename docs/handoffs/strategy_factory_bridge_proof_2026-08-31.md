# Strategy Factory Bridge Proof — 2026-08-31

## Outcome

The bounded external Strategy Factory bridge is implemented and verified for
artifact generation. Current-version NinjaTrader compilation remains
unverified because the runtime did not rebuild `NinjaTrader.Custom.dll` after
two observed file drops.

No strategy was enabled, no account or connection was changed, no optimizer was
run, and no order was submitted.

## Repository State

- Strategy Factory active home: `D:\ninjatrader-strategy-factory`
- Strategy Factory branch: `codex/project-modernization`
- ta_foundation canonical home: `D:\ta_foundation`
- ta_foundation bridge commit: `e592346`

## Bridge Verification

Command:

```powershell
D:\ta_foundation\.venv\Scripts\python.exe `
  -m ta_foundation.nt_strategy_loop.strategy_factory_bridge `
  --spec D:\ninjatrader-strategy-factory\strategy_factory\specs\examples\ema_cross_factory_bridge_proof.json `
  --out-dir D:\ta_foundation\.ta_artifacts\nt_strategy_lab\factory_builds\ema_cross_factory_bridge_proof_20260831
```

The bridge validated a manifest containing the normalized spec,
ta_foundation template JSON, NinjaScript C#, and Strategy Analyzer XML.

## Artifact Hashes

| Artifact | SHA-256 |
|---|---|
| `EmaCrossFactoryBridgeProof20260831.cs` | `077397939e19be4477d97513f8b259af2c374b45d89d42a62bfd1b2ab0ddde4c` |
| `EmaCrossFactoryBridgeProof20260831.strategy_spec.normalized.json` | `d9b05cc537c888a5d950f7e1dfc00717daa8060b454624612a28fd7faa9dc755` |
| `EmaCrossFactoryBridgeProof20260831.ta_template.json` | `487ac92f9cabfad1bdeae38a953db1b39e3529824f62f785c9ac518b49a3ed43` |
| `EmaCrossFactoryBridgeProof20260831Template.xml` | `56faa5bb86cb0819f9a9ec51e00c28a1ef920dbdcd7cc51bc1a2a80492deda9f` |

## NinjaTrader Observation

Installed version: NinjaTrader 8.1.8.1.

NinjaTrader readiness returned `state=ready`. The unique proof source was then
submitted twice through `observe-compile`. Both runs ended with:

- `state=timed_out`
- `compiled=false`
- `error_count=0`
- `No matching compiler errors were observed, but the strategy type is not
  visible in loaded NinjaTrader assemblies.`

Observation records:

- `.ta_artifacts/nt_strategy_lab/factory_builds/ema_cross_factory_bridge_proof_20260831/compile_observation.json`
- `.ta_artifacts/nt_strategy_lab/factory_builds/ema_cross_factory_bridge_proof_20260831/compile_observation_retry.json`

`NinjaTrader.Custom.dll` retained its 2026-08-17 timestamp throughout the
attempts. This is an operational compile-trigger gap, not evidence that the
generated C# compiled or failed compilation.

The unique installed proof file was moved out of the active Strategies folder
to the recoverable quarantine path:

`C:\ta_foundation\nt_compile_loop\quarantine\EmaCrossFactoryBridgeProof20260831.cs.20260831-uncompiled`

Its quarantined SHA-256 matches the generated C# hash above.

## Next Gate

Repair or re-prove the no-GUI NinjaTrader compile trigger. The acceptance check
is a changed `NinjaTrader.Custom.dll` timestamp plus a terminal `compiled=true`
observation for the uniquely named proof strategy. Only then may the optimizer
bridge run; optimization and parity must not be inferred from this proof.
