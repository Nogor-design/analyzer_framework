/* Discovery stage results pane.
 *
 * Renders the discovery_summary.json sidecar produced by a finished run:
 *   - Header strip (stage, instrument, generated_at, runtime)
 *   - Diagnostics chips (total combos, warnings)
 *   - Setup Cards grouped by tier (Most Robust → Marginal)
 *   - Per-card Promote buttons, one per recommended next stage
 *   - Next-stage recommendations footer
 *
 * Promotions hit POST /api/discovery/sessions/<sid>/promotions. Successful
 * promotions are reflected in a small toast and the optional
 * `onPromoted(promotion)` callback so the host can refresh state.
 */
(function (root) {
  "use strict";

  const W = root.DiscoveryWidgets;
  if (!W) {
    console.error("DiscoveryWidgets not loaded — widgets.js must come first.");
    return;
  }
  const { el } = W;

  // ---- Tier styling -----------------------------------------------------

  const TIER_STYLES = {
    most_robust:  {bg: "bg-emerald-500", fg: "text-emerald-950", label: "Most Robust",  border: "border-emerald-500"},
    high_quality: {bg: "bg-amber-400",   fg: "text-amber-950",   label: "High Quality", border: "border-amber-400"},
    solid:        {bg: "bg-sky-500",     fg: "text-sky-950",     label: "Solid",        border: "border-sky-500"},
    marginal:     {bg: "bg-gray-500",    fg: "text-gray-950",    label: "Marginal",     border: "border-gray-500"},
    rejected:     {bg: "bg-rose-700",    fg: "text-rose-100",    label: "Rejected",     border: "border-rose-700"},
  };

  const TIER_ORDER = ["most_robust", "high_quality", "solid", "marginal", "rejected"];

  function tierStyle(id) { return TIER_STYLES[id] || TIER_STYLES.marginal; }

  // ---- Format helpers --------------------------------------------------

  function fmtNum(value, digits) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return Number(value).toFixed(digits == null ? 2 : digits);
  }

  function fmtPct(value, digits) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return `${(Number(value) * 100).toFixed(digits == null ? 1 : digits)}%`;
  }

  function fmtInt(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return String(Math.trunc(Number(value)));
  }

  function prettyFamilyLabel(famId) {
    const map = {
      candle: "Candle", ma: "MA", orb: "ORB", bb: "Bollinger",
      lcr: "LCR", breakout: "Breakout", pullback: "Pullback", level: "Levels",
      large_candle_excursion: "Large Candle Excursion",
    };
    return map[famId] || famId;
  }

  function prettySignal(signalId) {
    if (!signalId) return "";
    return signalId.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // ---- Toast -----------------------------------------------------------

  function toast(message, kind) {
    const node = el("div", {
      class: `fixed bottom-6 right-6 z-50 px-4 py-2 rounded shadow text-sm ${
        kind === "error" ? "bg-rose-700 text-white" : "bg-emerald-600 text-white"
      }`,
    }, [message]);
    document.body.appendChild(node);
    setTimeout(() => {
      node.style.transition = "opacity 0.4s";
      node.style.opacity = "0";
      setTimeout(() => node.remove(), 400);
    }, 1800);
  }

  // ---- Card builder ----------------------------------------------------

  function metricChip(label, value) {
    return el("div", {class: "flex flex-col items-start px-2 py-1 bg-gray-900 border border-gray-800 rounded"}, [
      el("div", {class: "text-[10px] uppercase tracking-wider text-gray-500"}, [label]),
      el("div", {class: "text-sm font-semibold text-gray-100"}, [value]),
    ]);
  }

  function buildSetupCard(entry, deps) {
    const tier = entry.tier || {};
    const style = tierStyle(tier.id);
    const metrics = entry.metrics || {};

    // Summary header
    const variantCount = (entry.variants || []).length;
    const headerLeft = el("div", {class: "flex items-center gap-3 flex-wrap"}, [
      el("span", {class: `${style.bg} ${style.fg} text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded`}, [tier.label || style.label]),
      el("span", {class: "text-xs text-gray-500"}, [`#${entry.rank}`]),
      el("span", {class: "text-base font-semibold text-gray-100"}, [
        `${prettyFamilyLabel(entry.family)} · ${prettySignal(entry.signal)}`,
      ]),
      entry.timeframe ? el("span", {class: "text-xs text-amber-300"}, [entry.timeframe]) : null,
      entry.direction ? el("span", {class: "text-xs text-gray-400"}, [`(${entry.direction})`]) : null,
      variantCount
        ? el("span", {
            class: "text-[11px] px-2 py-0.5 rounded border border-amber-500/40 text-amber-300 bg-amber-500/10",
            title: `${variantCount} other combo(s) produced identical metrics on this dataset - their differing knobs didn't move the needle.`,
          }, [`+${variantCount} equivalent`])
        : null,
    ]);

    const verdictLine = tier.verdict
      ? el("div", {class: "text-xs text-gray-400 mt-0.5"}, [tier.verdict])
      : null;

    // Metrics row
    const metricsRow = el("div", {class: "flex flex-wrap gap-2 mt-3"}, [
      metricChip("PF",         fmtNum(metrics.profit_factor, 2)),
      metricChip("Trades",     fmtInt(metrics.trade_count)),
      metricChip("Win rate",   fmtPct(metrics.win_rate, 1)),
      metricChip("Expectancy", `${fmtNum(metrics.expectancy_ticks, 2)} t`),
      metrics.is_oos_degradation != null
        ? metricChip("IS/OOS deg", fmtPct(metrics.is_oos_degradation, 0))
        : null,
      metrics.max_drawdown_ticks != null
        ? metricChip("Max DD", `${fmtNum(metrics.max_drawdown_ticks, 0)} t`)
        : null,
      metrics.sharpe != null
        ? metricChip("Sharpe", fmtNum(metrics.sharpe, 2))
        : null,
    ]);

    // Explain block
    const explain = entry.explain || {};
    const explainBlock = el("div", {class: "mt-3 grid grid-cols-1 md:grid-cols-3 gap-2 text-xs text-gray-300"}, [
      el("div", {class: "p-2 bg-gray-900 border border-gray-800 rounded"}, [
        el("div", {class: "text-[10px] uppercase text-gray-500 mb-0.5"}, ["What it trades"]),
        explain.what_it_trades || "—",
      ]),
      el("div", {class: "p-2 bg-gray-900 border border-gray-800 rounded"}, [
        el("div", {class: "text-[10px] uppercase text-gray-500 mb-0.5"}, ["When it works"]),
        explain.when_it_works || "—",
      ]),
      el("div", {class: "p-2 bg-gray-900 border border-gray-800 rounded"}, [
        el("div", {class: "text-[10px] uppercase text-gray-500 mb-0.5"}, ["Risks"]),
        explain.risks || "—",
      ]),
    ]);

    // Promote buttons (one per recommended next stage)
    const payload = entry.promote_payload || {};
    const nextStages = payload.next_stages || [];
    const overrides = payload.yaml_overrides || {};
    const promoteRow = nextStages.length
      ? el("div", {class: "mt-3 flex flex-wrap items-center gap-2"}, [
          el("span", {class: "text-[11px] uppercase tracking-wider text-gray-500"}, ["Promote to"]),
          ...nextStages.map((toStage) => {
            const btn = el("button", {
              type: "button",
              class: "text-xs px-3 py-1 rounded border border-amber-500 text-amber-400 hover:bg-amber-500 hover:text-gray-900",
            }, [toStage]);
            btn.addEventListener("click", async () => {
              btn.disabled = true;
              const original = btn.textContent;
              btn.textContent = "…";
              try {
                const resp = await fetch(
                  `/api/discovery/sessions/${encodeURIComponent(deps.sessionId)}/promotions`,
                  {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                      from_stage: deps.fromStage,
                      to_stage: toStage,
                      rank: entry.rank,
                      yaml_overrides: overrides,
                      explain: explain.what_it_trades || "",
                    }),
                  },
                );
                if (!resp.ok) {
                  const data = await resp.json().catch(() => ({}));
                  toast(data.error || `Promotion failed: HTTP ${resp.status}`, "error");
                  btn.textContent = original;
                  btn.disabled = false;
                  return;
                }
                const data = await resp.json().catch(() => ({}));
                toast(`Promoted #${entry.rank} → ${toStage}`);
                btn.textContent = "Promoted ✓";
                btn.classList.remove("hover:bg-amber-500", "hover:text-gray-900");
                btn.classList.add("bg-amber-500", "text-gray-900");
                if (typeof deps.onPromoted === "function") deps.onPromoted(data.promotion || null);
              } catch (err) {
                toast(String(err), "error");
                btn.textContent = original;
                btn.disabled = false;
              }
            });
            return btn;
          }),
        ])
      : el("div", {class: "mt-3 text-xs text-gray-500 italic"}, [
          "No further stages recommended for this combo.",
        ]);

    // Optional details fold-out (params + outcome + variants)
    const detailsToggle = el("summary", {class: "text-xs text-gray-500 cursor-pointer hover:text-amber-400 mt-3"}, [
      variantCount
        ? `Show params, outcome, session filter, and ${variantCount} equivalent variant${variantCount === 1 ? "" : "s"}`
        : "Show params, outcome, session filter",
    ]);
    const detailsBody = el("div", {class: "mt-2 grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]"}, [
      kvBlock("Params", entry.params),
      kvBlock("Outcome", entry.outcome),
      kvBlock("Session filter", entry.session_filter),
    ]);
    const variantsBlock = variantCount ? buildVariantsBlock(entry.variants) : null;
    const details = el("details", {class: "mt-1"}, [
      detailsToggle,
      detailsBody,
      variantsBlock,
    ]);

    return el("div", {
      class: `border ${style.border} bg-gray-900/40 rounded-lg p-4`,
    }, [
      headerLeft,
      verdictLine,
      metricsRow,
      explainBlock,
      promoteRow,
      details,
    ]);
  }

  function kvBlock(title, obj) {
    const entries = Object.entries(obj || {});
    return el("div", {class: "p-2 bg-gray-900 border border-gray-800 rounded"}, [
      el("div", {class: "text-[10px] uppercase text-gray-500 mb-1"}, [title]),
      entries.length
        ? el("ul", {class: "space-y-0.5"}, entries.map(([k, v]) =>
            el("li", {class: "flex justify-between gap-2"}, [
              el("span", {class: "text-gray-500"}, [k]),
              el("span", {class: "text-gray-200 font-mono"}, [
                typeof v === "object" ? JSON.stringify(v) : String(v),
              ]),
            ])
          ))
        : el("div", {class: "text-gray-600 italic"}, ["(empty)"]),
    ]);
  }

  function buildVariantsBlock(variants) {
    // `variants` is the sidecar's per-row list; each entry has { params: {differing keys: values} }.
    if (!variants || !variants.length) return null;
    return el("div", {class: "mt-3 p-3 border border-amber-700/30 bg-amber-900/10 rounded text-[11px]"}, [
      el("div", {class: "text-[10px] uppercase tracking-wider text-amber-300 mb-1"}, [
        "Equivalent variants",
      ]),
      el("p", {class: "text-gray-400 mb-2 leading-relaxed"}, [
        "These combos produced identical metrics on this dataset - the listed parameter changes had no effect. Picking the simplest variant is fine.",
      ]),
      el("ul", {class: "space-y-1"}, variants.map((v, i) => {
        const params = (v && v.params) || {};
        const entries = Object.entries(params);
        return el("li", {class: "flex gap-2 items-center"}, [
          el("span", {class: "text-gray-500"}, [`#${i + 2}`]),
          entries.length
            ? el("span", {class: "text-gray-200 font-mono"}, [
                entries.map(([k, val]) => `${k}: ${typeof val === "object" ? JSON.stringify(val) : String(val)}`).join(", "),
              ])
            : el("span", {class: "text-gray-600 italic"}, ["(no param diff)"]),
        ]);
      })),
    ]);
  }

  // ---- Diagnostics + recommendations ------------------------------------

  function buildDiagnostics(summary) {
    const d = summary.diagnostics || {};
    const inp = summary.input_summary || {};
    const inst = summary.instrument || {};
    const stage = summary.stage || {};
    const chips = [
      el("span", {class: "px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-300"}, [
        `Stage: ${stage.label || stage.id}`,
      ]),
      el("span", {class: "px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-300"}, [
        `Instrument: ${inst.symbol}${inst.contract ? ` (${inst.contract})` : ""}`,
      ]),
      el("span", {class: "px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-300"}, [
        `Combos tested: ${fmtInt(d.total_combos_tested)} · passed: ${fmtInt(d.combos_passing_min_trades)}`,
      ]),
      d.runtime_seconds != null
        ? el("span", {class: "px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-300"}, [
            `Runtime: ${fmtInt(d.runtime_seconds)}s`,
          ])
        : null,
      inp.bar_count != null
        ? el("span", {class: "px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-300"}, [
            `Bars: ${fmtInt(inp.bar_count)}`,
          ])
        : null,
      summary.generated_at
        ? el("span", {class: "px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-500"}, [
            `Generated: ${summary.generated_at}`,
          ])
        : null,
    ];
    const warnings = (d.warnings || []).filter(Boolean);
    return el("div", {class: "flex flex-col gap-2"}, [
      el("div", {class: "flex flex-wrap gap-2"}, chips),
      warnings.length
        ? el("ul", {class: "text-xs text-amber-300 list-disc ml-5"}, warnings.map((w) => el("li", {}, [w])))
        : null,
    ]);
  }

  // Sentinel "action" ids the backend uses for empty-case recommendations.
  // The UI renders them with a friendly label and no funnel-stage code, since
  // they aren't real stages.
  const ACTION_LABELS = {
    "_action.longer_history":      "Run on a longer history",
    "_action.different_instrument": "Try a different instrument",
  };

  function buildNextStageBlock(summary) {
    const recs = summary.next_stage_recommendations || [];
    if (!recs.length) return null;
    // The empty-state banner already lists "longer history / different
    // instrument / try LCE" as bullets when the run had no edge. Avoid
    // showing the same recommendations a second time by suppressing this
    // block when the banner is up.
    const rankings = summary.rankings || [];
    const tb = (summary.diagnostics || {}).tier_breakdown || {};
    const allRejected =
      rankings.length > 0 && (tb.rejected || 0) === rankings.length;
    if (rankings.length === 0 || allRejected) return null;

    return el("section", {class: "mt-6 border border-amber-700/40 bg-amber-900/10 rounded p-4"}, [
      el("div", {class: "text-xs uppercase tracking-wider text-amber-300 mb-2"}, ["Next stages worth running"]),
      el("ul", {class: "space-y-1 text-sm text-amber-100"}, recs.map((r) => {
        const isAction = (r.stage_id || "").startsWith("_action.");
        const label = isAction
          ? (ACTION_LABELS[r.stage_id] || r.stage_id.replace("_action.", ""))
          : r.stage_id;
        return el("li", {}, [
          el("span", {class: isAction ? "text-amber-200" : "font-mono text-amber-300"}, [label]),
          " — ",
          r.reason || "",
        ]);
      })),
    ]);
  }

  // ---- Empty-state banner ----------------------------------------------

  /**
   * Decide whether the user is in an "empty" outcome and render a prominent
   * banner explaining it. Two cases:
   *   1. rankings.length === 0  -> nothing to show at all
   *   2. every ranking is in the rejected tier -> "no edge found"
   */
  function buildVerdictBanner(summary) {
    const rankings = summary.rankings || [];
    const diag = summary.diagnostics || {};
    const tb = diag.tier_breakdown || {};

    const allRejected =
      rankings.length > 0 && (tb.rejected || 0) === rankings.length;
    const isEmpty = rankings.length === 0;
    if (!isEmpty && !allRejected) return null;

    const headline = isEmpty ? "No setups to rank" : "No edge found";
    const reason = (diag.empty_reason || "").trim();
    const fallback = isEmpty
      ? "The sweep produced no rankable combos."
      : "Every ranked setup failed the quality gates.";

    const lines = [
      el("h3", {class: "text-base font-semibold text-amber-200"}, [headline]),
      el("p", {class: "text-sm text-gray-200 mt-1 leading-relaxed"}, [
        reason || fallback,
      ]),
    ];

    // What to try next — concrete, plain-English suggestions.
    const tips = [];
    const inp = summary.input_summary || {};
    if (inp.bar_count != null && inp.bar_count < 100_000) {
      tips.push("Run on a longer history (more weeks of data) before trusting any verdict.");
    }
    tips.push("Try a different instrument - some markets simply don't have edge for these signal families.");
    if ((summary.stage || {}).id !== "large_candle_excursion") {
      tips.push("Visit the Large Candle Excursion event-study page (LCE link in the header) to build stop/target intuition before sweeping again.");
    }
    if (isEmpty) {
      tips.push("If you set min_trades manually, try lowering it - too-high a threshold drops every combo.");
    }

    lines.push(el("ul", {class: "mt-3 list-disc ml-5 space-y-1 text-sm text-gray-300"},
      tips.map((t) => el("li", {}, [t]))));

    // Family coverage strip — which families produced ANY rows on this run.
    const fwr = diag.families_with_results || {};
    const familyEntries = Object.keys(fwr).sort();
    if (familyEntries.length) {
      lines.push(el("div", {class: "mt-4"}, [
        el("div", {class: "text-[11px] uppercase tracking-wider text-gray-400 mb-1"}, [
          "Family coverage on this run",
        ]),
        el("div", {class: "flex flex-wrap gap-2"}, familyEntries.map((fam) => {
          const count = fwr[fam] || 0;
          const ok = count > 0;
          const cls = ok
            ? "bg-emerald-900/40 text-emerald-200 border-emerald-700/40"
            : "bg-rose-900/30 text-rose-200 border-rose-700/40";
          return el("span", {
            class: `px-2 py-0.5 rounded border text-xs ${cls}`,
            title: ok ? `${count} combo(s) produced metrics` : "Tested but produced 0 rankable combos",
          }, [`${fam}: ${count}`]);
        })),
      ]));
    }

    return el("section", {class: "mt-4 mb-4 border border-amber-700/50 bg-amber-900/15 rounded p-4"}, lines);
  }

  // ---- Public API -------------------------------------------------------

  /**
   * Mount the results pane for a finished run.
   *
   * deps: {
   *   container: HTMLElement,
   *   sessionId,
   *   run,                  // StageRun dict
   *   onPromoted(p),        // optional callback after a successful promotion
   * }
   *
   * If the sidecar is not yet available (run still running, or summary file
   * missing) the pane renders a placeholder explaining what to do.
   */
  async function mountResultsPane(deps) {
    const { container, sessionId, run } = deps;
    container.replaceChildren();

    const placeholder = el("div", {class: "text-sm text-gray-400"}, [
      "Loading results…",
    ]);
    container.appendChild(placeholder);

    if (!run || !run.job_id) {
      placeholder.textContent = "Select a finished run from the right rail to see results.";
      return;
    }

    try {
      const resp = await fetch(
        `/api/discovery/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(run.job_id)}/summary`,
      );
      if (resp.status === 404) {
        const data = await resp.json().catch(() => ({}));
        const message = data && data.error ? data.error : "Sidecar not yet available.";
        placeholder.replaceWith(el("div", {class: "border border-gray-700 rounded p-4 text-sm text-gray-300 space-y-2"}, [
          el("div", {class: "text-amber-300 font-semibold"}, [message]),
          run.status === "succeeded"
            ? el("div", {}, [
                "The run finished but no discovery_summary.json was found at ",
                el("code", {class: "text-amber-300"}, [run.summary_json_path || "(unknown path)"]),
                ". This usually means the report YAML is missing the top-level discovery: block.",
              ])
            : el("div", {}, [`Run status: ${run.status}. Wait for it to finish.`]),
          run.report_html_path
            ? el("a", {
                href: `/api/artifact?path=${encodeURIComponent(run.report_html_path)}`,
                class: "inline-block mt-2 text-amber-400 hover:underline",
                target: "_blank",
                rel: "noopener",
              }, ["Open the HTML report →"])
            : null,
        ]));
        return;
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        placeholder.textContent = data.error || `Failed to load summary (HTTP ${resp.status}).`;
        return;
      }
      const data = await resp.json();
      placeholder.remove();
      renderSummary(container, data.summary || {}, {
        ...deps,
        run: data.run || run,
      });
    } catch (err) {
      placeholder.textContent = `Failed to load summary: ${err}`;
    }
  }

  function renderSummary(container, summary, deps) {
    const stage = summary.stage || {};
    const fromStage = stage.id || (deps.run && deps.run.stage_id) || "";

    // Header strip
    container.appendChild(el("section", {class: "mb-4 flex items-center justify-between"}, [
      el("div", {}, [
        el("h3", {class: "text-lg font-semibold text-gray-100"}, [
          `${stage.label || stage.id || "Results"}`,
        ]),
        el("div", {class: "text-xs text-gray-500"}, [
          `Run id: ${(deps.run && deps.run.job_id) || "—"}`,
        ]),
      ]),
      deps.run && deps.run.report_html_path
        ? el("a", {
            href: `/api/artifact?path=${encodeURIComponent(deps.run.report_html_path)}`,
            target: "_blank",
            rel: "noopener",
            class: "text-sm text-amber-400 hover:underline",
          }, ["Open full HTML report ↗"])
        : null,
    ]));

    container.appendChild(buildDiagnostics(summary));

    // Verdict banner — surfaces the "empty" or "all rejected" outcomes
    // before the cards so the user sees the headline first instead of
    // skimming a wall of red rejected cards.
    const verdict = buildVerdictBanner(summary);
    if (verdict) container.appendChild(verdict);

    // Group rankings by tier
    const rankings = summary.rankings || [];
    if (!rankings.length) {
      // Banner above already explained the empty state. Bail without
      // drawing the cards grid.
      return;
    }

    const grouped = {};
    for (const entry of rankings) {
      const tierId = (entry.tier && entry.tier.id) || "marginal";
      (grouped[tierId] = grouped[tierId] || []).push(entry);
    }

    const cardsHost = el("div", {class: "mt-6 space-y-6"}, []);
    for (const tierId of TIER_ORDER) {
      const list = grouped[tierId];
      if (!list || !list.length) continue;
      const style = tierStyle(tierId);
      cardsHost.appendChild(el("section", {}, [
        el("div", {class: "flex items-baseline gap-2 mb-2"}, [
          el("h4", {class: `text-sm uppercase tracking-wider font-semibold ${tierId === "rejected" ? "text-rose-300" : "text-amber-300"}`}, [
            style.label,
          ]),
          el("span", {class: "text-xs text-gray-500"}, [`${list.length} combo${list.length === 1 ? "" : "s"}`]),
        ]),
        el("div", {class: "space-y-3"}, list.map((entry) => buildSetupCard(entry, {
          sessionId: deps.sessionId,
          fromStage,
          onPromoted: deps.onPromoted,
        }))),
      ]));
    }
    container.appendChild(cardsHost);

    const nextBlock = buildNextStageBlock(summary);
    if (nextBlock) container.appendChild(nextBlock);
  }

  root.DiscoveryResults = { mountResultsPane };
})(window);
