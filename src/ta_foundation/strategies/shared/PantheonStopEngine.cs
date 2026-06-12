/*
 * ═══════════════════════════════════════════════════════════════════════════════
 *  PantheonStopEngine — pure stop/trail math shared by Strategy + AddOn + tests
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 *  WHY THIS FILE EXISTS
 *  ─────────────────────────────────────────────────────────────────────────────
 *  The stop-in-profit / trail logic used to live inside PantheonMaster (a Strategy),
 *  so it could only be exercised through a live or Playback Strategy — one position
 *  at a time. This class lifts that math out into plain C# with ZERO NinjaTrader
 *  dependencies, so the identical logic can be driven by:
 *    1. PantheonMaster (live path delegates to it),
 *    2. the TaFoundation AddOn live-test harness (Account-API order driver),
 *    3. a C# unit test / battery harness (no NT at all),
 *    4. and mirrored 1:1 by the Python replica
 *       (src/ta_foundation/analysis/exits/nt_atr_trail_parity.py).
 *
 *  BUILD / LINKING
 *  ─────────────────────────────────────────────────────────────────────────────
 *  Because it has no NT references, this single source file is LINKED into both
 *  the external Visual Studio AddOn project (<Compile Include="..\..\shared\
 *  PantheonStopEngine.cs" />) and dropped into NinjaScript custom for the Strategy.
 *  One source of truth — do not fork it.
 *
 *  FAITHFULNESS
 *  ─────────────────────────────────────────────────────────────────────────────
 *  Every branch mirrors PantheonMaster.cs exactly (line refs noted per method as of
 *  the extraction). The engine is PURE: callers own the running state (favorable
 *  extreme, peak open profit, breakEvenActive, lastSubmittedStop) and the live
 *  side-of-market guard + actual order submission. The engine only answers
 *  "given this state, what stop price does the policy want, and does it improve?"
 * ═══════════════════════════════════════════════════════════════════════════════
 */

using System;

namespace TaFoundation.Pantheon
{
	/// <summary>
	/// Exit policy selector. Distinct from the Strategy's NinjaScript
	/// <c>PantheonExitPolicy</c> enum (which is serialized into workspaces) so this
	/// shared class carries no NinjaScript coupling. Callers map their enum to this.
	/// </summary>
	public enum PantheonStopPolicy
	{
		FixedRR       = 0,
		AtrTrail      = 1,
		Chandelier    = 2,
		Giveback      = 3,
		FixedStop     = 4,
		BreakEvenOnly = 5
	}

	/// <summary>Immutable per-trade configuration (mirror of the strategy's Discovery Exit params).</summary>
	public struct PantheonStopConfig
	{
		public PantheonStopPolicy Policy;
		public double TickSize;
		public double PointValue;

		public int    StopTicks;             // initial protective stop distance (ticks)
		public int    TargetTicks;           // FixedRR target distance (ticks)
		public double AtrTrailMultiple;      // AtrTrail / Chandelier ATR multiple
		public int    ChandelierLookback;    // bars (caller supplies the ref price)
		public double GivebackPct;           // fraction of peak open profit to give back
		public int    BreakEvenTriggerTicks; // profit (ticks) before BE arms
		public int    BreakEvenPlusTicks;    // BE stop offset above/below entry (ticks)
	}

	/// <summary>Result of a trail computation. <see cref="HasProposal"/> false means leave the stop where it is.</summary>
	public struct PantheonTrailResult
	{
		public bool   HasProposal;        // a (rounded) proposed stop price is present
		public double ProposedStop;       // rounded to tick; only valid when HasProposal
		public bool   BreakEvenActivated; // BreakEvenOnly: true on the tick BE first arms
	}

	/// <summary>
	/// Pure stop/trail math. All methods are static and side-effect free; the caller
	/// owns and threads the running state between calls.
	/// </summary>
	public static class PantheonStopEngine
	{
		public const int Long  =  1;
		public const int Short = -1;

		/// <summary>Round a price to the instrument tick grid (mirror of RoundToTickSize).</summary>
		public static double RoundToTick(double price, double tickSize)
		{
			if (tickSize <= 0.0) return price;
			return Math.Round(price / tickSize) * tickSize;
		}

		/// <summary>
		/// Initial protective stop price. Mirror of PantheonMaster.GetInitialStopPrice
		/// (discovery path): StopTicks below entry for long / above for short.
		/// </summary>
		public static double InitialStop(int dir, double entryPrice, PantheonStopConfig cfg)
		{
			return RoundToTick(entryPrice - dir * cfg.StopTicks * cfg.TickSize, cfg.TickSize);
		}

		/// <summary>True only for FixedRR — the one discovery policy that carries a target (ShouldUseTarget).</summary>
		public static bool UsesTarget(PantheonStopConfig cfg)
		{
			return cfg.Policy == PantheonStopPolicy.FixedRR;
		}

		/// <summary>Initial target price (FixedRR). Mirror of GetInitialTargetPrice (discovery path).</summary>
		public static double InitialTarget(int dir, double entryPrice, PantheonStopConfig cfg)
		{
			return RoundToTick(entryPrice + dir * cfg.TargetTicks * cfg.TickSize, cfg.TickSize);
		}

		/// <summary>
		/// Ratchet test. Mirror of MoveStopIfImproved / MoveHistoricalStopIfImproved:
		/// a long stop must rise &gt; 0.5 tick above the last stop; a short must fall the same.
		/// </summary>
		public static bool Improves(int dir, double proposedStop, double lastStop, double tickSize)
		{
			if (dir == Long)  return proposedStop > lastStop + tickSize * 0.5;
			return proposedStop < lastStop - tickSize * 0.5;
		}

		/// <summary>Open profit in account currency at <paramref name="price"/>. Mirror of GetOpenProfitCurrencyFromPrice.</summary>
		public static double OpenProfitCurrency(int dir, double price, double entryPrice, double pointValue, int quantity)
		{
			if (quantity <= 0) return 0.0;
			double points = dir == Long ? price - entryPrice : entryPrice - price;
			return points * pointValue * quantity;
		}

		/// <summary>
		/// Core trail computation — the exact per-policy switch from
		/// PantheonMaster.ManageLiveDynamicStop, with NO NinjaScript dependency.
		///
		/// The caller maintains and passes the running state:
		///   favorableExtreme       — running max(price) for a long / running min for a short,
		///                            updated tick-by-tick (live) or bar-by-bar (backtest).
		///   peakOpenProfitCurrency — running max open profit ($), for Giveback.
		///   chandelierRefPrice     — long: highest High over ChandelierLookback bars;
		///                            short: lowest Low. Pass 0 if not using Chandelier.
		///   breakEvenActive        — whether BreakEvenOnly has already armed this trade.
		///
		/// Returns a (rounded) proposed stop when the policy wants one; the caller still
		/// applies <see cref="Improves"/> (ratchet) and its own side-of-market guard before
		/// submitting. For BreakEvenOnly, <see cref="PantheonTrailResult.BreakEvenActivated"/>
		/// tells the caller to flip its breakEvenActive flag.
		/// </summary>
		public static PantheonTrailResult ComputeTrailStop(
			int dir,
			double entryPrice,
			double triggerPrice,
			double favorableExtreme,
			double currentAtr,
			double peakOpenProfitCurrency,
			int quantity,
			double chandelierRefPrice,
			bool breakEvenActive,
			PantheonStopConfig cfg)
		{
			var result = new PantheonTrailResult { HasProposal = false, ProposedStop = 0.0, BreakEvenActivated = false };
			double tick = cfg.TickSize;

			switch (cfg.Policy)
			{
				case PantheonStopPolicy.BreakEvenOnly:
				{
					if (breakEvenActive) break;
					double triggerLevel = entryPrice + dir * cfg.BreakEvenTriggerTicks * tick;
					bool armed = dir == Long ? triggerPrice >= triggerLevel : triggerPrice <= triggerLevel;
					if (armed)
					{
						result.BreakEvenActivated = true;
						result.HasProposal  = true;
						result.ProposedStop = RoundToTick(entryPrice + dir * cfg.BreakEvenPlusTicks * tick, tick);
					}
					break;
				}

				case PantheonStopPolicy.AtrTrail:
				{
					double proposed = favorableExtreme - dir * cfg.AtrTrailMultiple * currentAtr;
					result.HasProposal  = true;
					result.ProposedStop = RoundToTick(proposed, tick);
					break;
				}

				case PantheonStopPolicy.Chandelier:
				{
					// long: pull from the higher of (lookback high, favorable extreme); short: the lower.
					double refPrice = dir == Long
						? Math.Max(chandelierRefPrice, favorableExtreme)
						: Math.Min(chandelierRefPrice, favorableExtreme);
					double proposed = refPrice - dir * cfg.AtrTrailMultiple * currentAtr;
					result.HasProposal  = true;
					result.ProposedStop = RoundToTick(proposed, tick);
					break;
				}

				case PantheonStopPolicy.Giveback:
				{
					if (peakOpenProfitCurrency > 0.0)
					{
						double protectedProfit = peakOpenProfitCurrency * (1.0 - cfg.GivebackPct);
						double proposed = StopPriceFromProtectedProfit(dir, entryPrice, protectedProfit, cfg.PointValue, quantity);
						result.HasProposal  = true;
						result.ProposedStop = RoundToTick(proposed, tick);
					}
					break;
				}

				// FixedRR / FixedStop: the initial stop never trails.
				case PantheonStopPolicy.FixedRR:
				case PantheonStopPolicy.FixedStop:
				default:
					break;
			}

			return result;
		}

		/// <summary>Mirror of GetStopPriceFromProtectedOpenProfit.</summary>
		public static double StopPriceFromProtectedProfit(int dir, double entryPrice, double protectedProfitCurrency, double pointValue, int quantity)
		{
			double denom = pointValue * Math.Max(1, quantity);
			double protectedPoints = denom == 0.0 ? 0.0 : protectedProfitCurrency / denom;
			return dir == Long ? entryPrice + protectedPoints : entryPrice - protectedPoints;
		}
	}
}
