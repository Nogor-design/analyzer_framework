/* Discovery stage configure form.
 *
 * Drives the middle pane of /discovery: project context (input/output/market
 * folders), enabled signal families, and a small set of common parameter
 * overrides. Auto-saves to the session, debounced. On every change, builds
 * a preview by calling /api/discovery/stages/<id>/preview so the user sees
 * the generated YAML and validation feedback live.
 *
 * Each stage gets its enabled-families checkbox group from the stage
 * definition. Common-parameter widgets are rendered conditionally:
 * fields like "TP/SL ticks" or "session_filter" are only shown when at
 * least one family in the stage actually consumes them.
 */
(function (root) {
  "use strict";

  const W = root.DiscoveryWidgets;
  if (!W) {
    console.error("DiscoveryWidgets not loaded — widgets.js must come first.");
    return;
  }
  const { el } = W;

  // ---- Schema metadata --------------------------------------------------

  // Which families' default YAML blocks accept a top-level `session_filter`,
  // `min_trades`, `outcome.ticks.{take_profit,stop}`, or `timeframes` key.
  // The actual default values come from the stage's default_yaml so the
  // widgets show the right starting point per stage.
  const FAMILY_BLOCKS = [
    "candle_discovery",
    "ma_discovery",
    "orb_discovery",
    "bb_discovery",
    "lcr_discovery",
    "breakout_discovery",
    "pullback_discovery",
    "level_discovery",
  ];

  // ---- Default-yaml lookup helpers -------------------------------------

  function getFromYaml(yamlObj, dottedPath) {
    let cur = yamlObj;
    for (const part of dottedPath.split(".")) {
      if (cur == null || typeof cur !== "object") return undefined;
      cur = cur[part];
    }
    return cur;
  }

  function firstFamilyValue(stage, dottedPath, fallback) {
    if (!stage || !stage.default_yaml) return fallback;
    for (const fam of stage.enabled_families || []) {
      const block = stage.default_yaml[`${fam}_discovery`] ||
                    stage.default_yaml[fam];
      if (!block) continue;
      const val = getFromYaml(block, dottedPath);
      if (val !== undefined) return val;
    }
    return fallback;
  }

  // ---- Override builder -------------------------------------------------

  /**
   * Translate the form's flat values into the deep-merge override dict
   * that build_stage_payload (server) accepts. Only sets keys whose
   * checkbox toggles are on AND which differ from the stage default.
   */
  function buildOverrides(stage, values, familiesById) {
    familiesById = familiesById || {};
    const overrides = {};
    const enabledFamilies = stage.enabled_families || [];

    function applyToFamilies(dottedPath, value) {
      // Apply same value into every enabled family's yaml_block IF the
      // default tree already has that path defined for that block.
      for (const fam of enabledFamilies) {
        const blockName = `${fam}_discovery`;
        const baseBlock = (stage.default_yaml || {})[blockName];
        if (!baseBlock) continue;
        if (getFromYaml(baseBlock, dottedPath) === undefined) continue;
        overrides[blockName] = overrides[blockName] || {};
        setDotted(overrides[blockName], dottedPath, value);
      }
    }

    // Sub-signal disable overrides — only emit overrides for unticked entries.
    const subSignals = (values && values.sub_signals) || {};
    const stillEnabled = (values && values.enabled_families) || [];
    for (const famId of Object.keys(subSignals)) {
      if (stillEnabled.indexOf(famId) === -1) continue;
      const fam = familiesById[famId];
      if (!fam || !Array.isArray(fam.sub_signals) || !fam.sub_signals.length) continue;
      const allIds = fam.sub_signals.map((s) => s.id);
      const ticked = new Set(subSignals[famId] || []);
      const offIds = allIds.filter((id) => !ticked.has(id));
      const blockName = (famId === "large_candle_excursion") ? "large_candle_excursion" : `${famId}_discovery`;
      if (famId === "lcr") {
        // LCR uses signal_types: list — write only the ticked subset.
        overrides[blockName] = overrides[blockName] || {};
        overrides[blockName].signal_types = Array.from(ticked);
      } else if (famId === "candle") {
        if (offIds.length) {
          overrides[blockName] = overrides[blockName] || {};
          overrides[blockName].patterns = overrides[blockName].patterns || {};
          for (const id of offIds) {
            overrides[blockName].patterns[id] = {enabled: false};
          }
        }
      } else if (offIds.length) {
        overrides[blockName] = overrides[blockName] || {};
        overrides[blockName].signals = overrides[blockName].signals || {};
        for (const id of offIds) {
          overrides[blockName].signals[id] = {enabled: false};
        }
      }
    }

    if (values.override_min_trades && Number.isFinite(values.min_trades)) {
      applyToFamilies("min_trades", values.min_trades);
    }
    if (values.override_timeframes && Array.isArray(values.timeframes) && values.timeframes.length) {
      applyToFamilies("timeframes", values.timeframes);
    }
    if (values.override_session && values.session_hour_from != null) {
      applyToFamilies("session_filter.hour_from", values.session_hour_from);
      applyToFamilies("session_filter.minute_from", values.session_minute_from || 0);
      applyToFamilies("session_filter.hour_to", values.session_hour_to);
    }
    if (values.override_tp_sl) {
      const tp = Number.isFinite(values.tp_ticks) ? [values.tp_ticks] : null;
      const sl = Number.isFinite(values.sl_ticks) ? [values.sl_ticks] : null;
      if (tp) applyToFamilies("outcome.ticks.take_profit", tp);
      if (sl) applyToFamilies("outcome.ticks.stop", sl);
      // LCR uses a different shape — flat tp_ticks/sl_ticks at the block root.
      if ((stage.enabled_families || []).indexOf("lcr") !== -1) {
        if (tp || sl) {
          overrides["lcr_discovery"] = overrides["lcr_discovery"] || {};
          if (tp) overrides["lcr_discovery"].tp_ticks = tp;
          if (sl) overrides["lcr_discovery"].sl_ticks = sl;
        }
      }
    }

    // Stage 6 — merge in selected promotions' yaml_overrides on top.
    if (stage.depends_on_promotions && Array.isArray(values.selected_promotions_payload)) {
      for (const promoOverrides of values.selected_promotions_payload) {
        deepMerge(overrides, promoOverrides || {});
      }
    }

    return overrides;
  }

  function setDotted(obj, dottedPath, value) {
    const parts = dottedPath.split(".");
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      const k = parts[i];
      if (cur[k] == null || typeof cur[k] !== "object") cur[k] = {};
      cur = cur[k];
    }
    cur[parts[parts.length - 1]] = value;
  }

  function deepMerge(target, source) {
    if (!source || typeof source !== "object") return target;
    for (const key of Object.keys(source)) {
      const sv = source[key];
      const tv = target[key];
      if (sv && typeof sv === "object" && !Array.isArray(sv)
          && tv && typeof tv === "object" && !Array.isArray(tv)) {
        deepMerge(tv, sv);
      } else {
        target[key] = Array.isArray(sv) ? sv.slice() : sv;
      }
    }
    return target;
  }

  // ---- Main controller --------------------------------------------------

  /**
   * Mount the configure pane for a stage.
   *
   * deps: {
   *   container: HTMLElement,         // where to render
   *   stage,                          // full stage dict (with default_yaml)
   *   sessionId,                      // active session id
   *   sessionDoc,                     // current session document JSON
   *   onRunDispatched(run),           // callback after a run is queued
   * }
   */
  function mountStageForm(deps) {
    const { container, stage, sessionId, sessionDoc, onRunDispatched } = deps;
    const familiesById = deps.familiesById || {};
    container.replaceChildren();

    const ctx = (sessionDoc && sessionDoc.context) || {};
    const savedFormValues =
      (sessionDoc && sessionDoc.stage_form_values && sessionDoc.stage_form_values[stage.id]) || {};
    const savedSubSignals = (savedFormValues.sub_signals && typeof savedFormValues.sub_signals === "object")
      ? savedFormValues.sub_signals
      : {};

    // ---- Build widgets ----

    const widgets = {};

    // Project context
    widgets.input_folder = W.createTextField({
      id: "input_folder",
      label: "Input folder (NinjaTrader exports)",
      value: ctx.input_folder || "",
      placeholder: "C:/path/to/exports",
      help: "Discovery still calls the same CLI; it expects an --input folder even when the stage runs purely on market data.",
      glossaryTerm: "input_folder",
    });
    widgets.output_folder = W.createTextField({
      id: "output_folder",
      label: "Output folder",
      value: ctx.output_folder || "",
      placeholder: "./outputs/discovery",
      help: "Where discovery_summary.json and the HTML report land.",
    });
    widgets.market_data_folder = W.createTextField({
      id: "market_data_folder",
      label: "Market data folder",
      value: ctx.market_data_folder || "",
      placeholder: "D:/MarketData",
      glossaryTerm: "market_data",
    });
    widgets.recursive = W.createToggle({
      id: "recursive",
      label: "Recurse into subfolders",
      value: ctx.recursive !== false,
    });
    widgets.no_tick_data = W.createToggle({
      id: "no_tick_data",
      label: "Skip tick data (faster)",
      value: ctx.no_tick_data !== false,
      help: "Discovery never needs tick data. Leave on unless you specifically want LCE tick analysis.",
    });

    // Family enable/disable
    const familyOptions = (stage.enabled_families || []).map((famId) => ({
      value: famId,
      label: prettyFamilyLabel(famId),
      glossaryTerm: `${famId}_family`,
    }));
    const initialEnabled = Array.isArray(savedFormValues.enabled_families)
      ? savedFormValues.enabled_families
      : (stage.enabled_families || []).slice();
    widgets.enabled_families = W.createCheckboxGroup({
      id: "enabled_families",
      label: "Signal families to test in this stage",
      options: familyOptions,
      value: initialEnabled,
      help: "Untick a family to disable its block in the generated YAML.",
    });

    // Common-parameter overrides — only render if the stage's defaults actually have these.
    const defaultMinTrades = firstFamilyValue(stage, "min_trades", null);
    const defaultTimeframes = firstFamilyValue(stage, "timeframes", null);
    const defaultSessionHourFrom = firstFamilyValue(stage, "session_filter.hour_from", null);
    const defaultSessionMinuteFrom = firstFamilyValue(stage, "session_filter.minute_from", null);
    const defaultSessionHourTo = firstFamilyValue(stage, "session_filter.hour_to", null);
    const defaultTp = firstArrayHead(firstFamilyValue(stage, "outcome.ticks.take_profit", null));
    const defaultSl = firstArrayHead(firstFamilyValue(stage, "outcome.ticks.stop", null));

    widgets.override_min_trades = W.createToggle({
      id: "override_min_trades",
      label: `Override min_trades${defaultMinTrades != null ? ` (default ${defaultMinTrades})` : ""}`,
      value: Boolean(savedFormValues.override_min_trades),
      glossaryTerm: "min_trades",
    });
    widgets.min_trades = W.createNumberField({
      id: "min_trades",
      label: "min_trades",
      value: savedFormValues.min_trades != null ? savedFormValues.min_trades : defaultMinTrades,
      min: 1,
      step: 1,
    });

    widgets.override_timeframes = W.createToggle({
      id: "override_timeframes",
      label: "Override timeframes",
      value: Boolean(savedFormValues.override_timeframes),
      glossaryTerm: "timeframe",
    });
    widgets.timeframes = W.createNumberListField({
      id: "timeframes",
      label: "Timeframes (minutes)",
      value: Array.isArray(savedFormValues.timeframes) && savedFormValues.timeframes.length
        ? savedFormValues.timeframes
        : (Array.isArray(defaultTimeframes) ? defaultTimeframes : []),
      placeholder: "e.g. 1, 5",
      help: "Comma-separated list of timeframes in minutes. 1 and 5 are typical.",
    });

    widgets.override_session = W.createToggle({
      id: "override_session",
      label: "Override session filter",
      value: Boolean(savedFormValues.override_session),
      glossaryTerm: "session_filter",
    });
    widgets.session_hour_from = W.createNumberField({
      id: "session_hour_from",
      label: "From hour (Denver)",
      value: savedFormValues.session_hour_from != null ? savedFormValues.session_hour_from : defaultSessionHourFrom,
      min: 0,
      max: 23,
      step: 1,
    });
    widgets.session_minute_from = W.createNumberField({
      id: "session_minute_from",
      label: "From minute",
      value: savedFormValues.session_minute_from != null ? savedFormValues.session_minute_from : (defaultSessionMinuteFrom || 0),
      min: 0,
      max: 59,
      step: 1,
    });
    widgets.session_hour_to = W.createNumberField({
      id: "session_hour_to",
      label: "To hour (Denver)",
      value: savedFormValues.session_hour_to != null ? savedFormValues.session_hour_to : defaultSessionHourTo,
      min: 0,
      max: 23,
      step: 1,
    });

    widgets.override_tp_sl = W.createToggle({
      id: "override_tp_sl",
      label: `Override TP / SL ticks${defaultTp != null ? ` (default TP=${defaultTp}, SL=${defaultSl})` : ""}`,
      value: Boolean(savedFormValues.override_tp_sl),
      glossaryTerm: "tp_sl",
    });
    widgets.tp_ticks = W.createNumberField({
      id: "tp_ticks",
      label: "Take-profit (ticks)",
      value: savedFormValues.tp_ticks != null ? savedFormValues.tp_ticks : defaultTp,
      min: 1,
      step: 1,
    });
    widgets.sl_ticks = W.createNumberField({
      id: "sl_ticks",
      label: "Stop-loss (ticks)",
      value: savedFormValues.sl_ticks != null ? savedFormValues.sl_ticks : defaultSl,
      min: 1,
      step: 1,
    });

    // ---- Layout ----

    const ctxSection = el("section", {class: "dw-section"}, [
      el("div", {class: "dw-section-title"}, ["Project Context"]),
      el("div", {class: "grid grid-cols-1 gap-3"}, [
        widgets.input_folder.el,
        widgets.output_folder.el,
        widgets.market_data_folder.el,
        el("div", {class: "dw-field-row"}, [
          widgets.recursive.el,
          widgets.no_tick_data.el,
        ]),
      ]),
    ]);

    const familySection = el("section", {class: "dw-section"}, [
      el("div", {class: "dw-section-title"}, ["Signal Families"]),
      widgets.enabled_families.el,
    ]);

    // Sub-signal selectors per family — re-rendered when family enable/disable
    // changes so disabled families' selectors disappear.
    const subSignalsHost = el("section", {class: "dw-section"}, []);
    const subSignalWidgets = {};   // family_id -> widget

    function rebuildSubSignals() {
      subSignalsHost.replaceChildren();
      const enabled = widgets.enabled_families.get() || [];
      const candidates = enabled
        .map((fid) => familiesById[fid])
        .filter((fam) => fam && Array.isArray(fam.sub_signals) && fam.sub_signals.length);
      if (!candidates.length) {
        return;
      }
      subSignalsHost.appendChild(el("div", {class: "dw-section-title"}, ["Sub-signals to include"]));
      for (const fam of candidates) {
        const allIds = fam.sub_signals.map((s) => s.id);
        const initial = Array.isArray(savedSubSignals[fam.id]) ? savedSubSignals[fam.id] : allIds.slice();
        const widget = W.createCheckboxGroup({
          id: `sub_signals_${fam.id}`,
          label: prettyFamilyLabel(fam.id),
          options: fam.sub_signals.map((s) => ({
            value: s.id,
            label: s.label || s.id,
            glossaryTerm: s.glossary_term,
          })),
          value: initial,
          help: "Untick a sub-signal to skip it for this run.",
        });
        widget.onChange(() => {
          debouncedSave();
          debouncedPreview();
        });
        subSignalWidgets[fam.id] = widget;
        subSignalsHost.appendChild(el("details", {open: candidates.length === 1 ? "open" : false, class: "mb-3"}, [
          el("summary", {class: "text-sm text-gray-300 cursor-pointer hover:text-amber-400"}, [
            `${prettyFamilyLabel(fam.id)} — ${initial.length}/${allIds.length} enabled`,
          ]),
          el("div", {class: "mt-2"}, [widget.el]),
        ]));
      }
    }

    const commonSection = el("section", {class: "dw-section"}, [
      el("div", {class: "dw-section-title"}, ["Common Parameters (optional overrides)"]),
      el("div", {class: "space-y-3"}, [
        defaultMinTrades != null ? el("div", {class: "dw-field-row"}, [
          widgets.override_min_trades.el,
          widgets.min_trades.el,
        ]) : null,
        defaultTimeframes != null ? el("div", {class: "dw-field-row"}, [
          widgets.override_timeframes.el,
          widgets.timeframes.el,
        ]) : null,
        defaultSessionHourFrom != null ? el("div", {class: "dw-field-row"}, [
          widgets.override_session.el,
          widgets.session_hour_from.el,
          widgets.session_minute_from.el,
          widgets.session_hour_to.el,
        ]) : null,
        defaultTp != null ? el("div", {class: "dw-field-row"}, [
          widgets.override_tp_sl.el,
          widgets.tp_ticks.el,
          widgets.sl_ticks.el,
        ]) : null,
      ]),
    ]);

    // ---- Expert mode: free-form JSON overrides ----
    // The textarea content is parsed as JSON, deep-merged on top of the
    // overrides built from the form widgets, and the resulting tree is sent
    // to /preview and to /runs. Hidden when window.DiscoveryExpertMode is off.
    const expertSavedJson = (typeof savedFormValues.expert_overrides_json === "string")
      ? savedFormValues.expert_overrides_json
      : "";
    const expertTextarea = el("textarea", {
      class: "dw-textarea",
      rows: "8",
      placeholder: '{\n  "candle_discovery": {\n    "min_trades": 50\n  }\n}',
      spellcheck: "false",
    });
    expertTextarea.value = expertSavedJson;
    const expertStatus = el("div", {class: "dw-helptext mt-1"}, [
      "JSON object. Deep-merged on top of the form-built overrides before dispatch.",
    ]);
    const expertSection = el("section", {class: "dw-section"}, [
      el("div", {class: "dw-section-title"}, ["Expert overrides (raw JSON)"]),
      el("p", {class: "text-xs text-gray-500 mb-2"}, [
        "Beginner-mode form fields cover the common knobs. Use this for everything else: " +
        "tweak threshold lists, swap signal types, override block-specific keys. ",
        "These overrides are deep-merged on top of the form output, so anything you set wins.",
      ]),
      expertTextarea,
      expertStatus,
    ]);
    // Visibility is controlled by window.DiscoveryExpertMode at mount time.
    if (!root.DiscoveryExpertMode) {
      expertSection.classList.add("hidden");
    }

    function getExpertOverrides() {
      const raw = (expertTextarea.value || "").trim();
      if (!raw) {
        expertStatus.textContent = "JSON object. Deep-merged on top of the form-built overrides before dispatch.";
        expertStatus.classList.remove("text-rose-300", "text-emerald-300");
        return {ok: true, overrides: {}};
      }
      try {
        const parsed = JSON.parse(raw);
        if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
          expertStatus.textContent = "Top-level value must be a JSON object.";
          expertStatus.classList.remove("text-emerald-300");
          expertStatus.classList.add("text-rose-300");
          return {ok: false, overrides: {}};
        }
        expertStatus.textContent = "✓ Expert overrides parsed.";
        expertStatus.classList.remove("text-rose-300");
        expertStatus.classList.add("text-emerald-300");
        return {ok: true, overrides: parsed};
      } catch (err) {
        expertStatus.textContent = `JSON parse error: ${err.message}`;
        expertStatus.classList.remove("text-emerald-300");
        expertStatus.classList.add("text-rose-300");
        return {ok: false, overrides: {}};
      }
    }
    expertTextarea.addEventListener("input", () => {
      getExpertOverrides();
      debouncedSave();
      debouncedPreview();
    });

    // Preview pane + run button
    const previewBox = el("pre", {class: "dw-yaml-preview"}, ["(generating preview…)"]);
    const validationBox = el("div", {class: "mt-2"}, []);
    const commandBox = el("pre", {class: "dw-yaml-preview mt-2"}, [""]);
    const runBtn = el("button", {class: "dw-run-btn", type: "button"}, ["Run this stage"]);
    runBtn.disabled = true;

    const watchHost = el("div", {class: "mt-4 hidden"}, []);
    const previewSection = el("section", {class: "dw-section"}, [
      el("div", {class: "dw-section-title flex items-center justify-between"}, [
        el("span", {}, ["Generated YAML"]),
        el("span", {class: "text-[10px] text-gray-500 normal-case tracking-normal"}, [
          "Updates as you edit",
        ]),
      ]),
      previewBox,
      el("div", {class: "dw-section-title mt-3"}, ["CLI command"]),
      commandBox,
      validationBox,
      el("div", {class: "mt-4 flex items-center gap-3"}, [
        runBtn,
        el("span", {class: "text-xs text-gray-500"}, ["Dispatches the same command shown above as a background job."]),
      ]),
      watchHost,
    ]);

    // Stage 6 only — promotions-driven validation form.
    const promotionsState = { items: [], selected: new Set() };
    const promotionsSection = el("section", {class: "dw-section"}, []);

    container.appendChild(ctxSection);
    container.appendChild(familySection);
    container.appendChild(subSignalsHost);
    container.appendChild(commonSection);
    container.appendChild(expertSection);
    if (stage.depends_on_promotions) {
      // Hide the family/sub-signal/common sections — Stage 6 sources its
      // YAML overrides entirely from the user-promoted combos.
      familySection.classList.add("hidden");
      subSignalsHost.classList.add("hidden");
      commonSection.classList.add("hidden");
      container.appendChild(promotionsSection);
    }
    container.appendChild(previewSection);

    rebuildSubSignals();
    widgets.enabled_families.onChange(() => {
      rebuildSubSignals();
    });

    function renderPromotions() {
      promotionsSection.replaceChildren();
      promotionsSection.appendChild(el("div", {class: "dw-section-title"}, ["Promoted Combos to Validate"]));
      promotionsSection.appendChild(el("div", {class: "text-xs text-gray-500 mb-3"}, [
        "Stage 6 walk-forward-validates the combos you promoted from earlier stages. " +
        "Pick which ones to include — each one's parameters get merged into the run YAML.",
      ]));
      if (!promotionsState.items.length) {
        promotionsSection.appendChild(el("div", {class: "p-4 border border-dashed border-gray-700 rounded text-sm text-gray-400"}, [
          "No combos have been promoted to this stage yet. Go back to an earlier stage's Results tab and click ‘Promote to ",
          stage.id,
          "’ on at least one combo first.",
        ]));
        return;
      }
      const list = el("div", {class: "space-y-2"}, []);
      promotionsState.items.forEach((p, i) => {
        const checked = promotionsState.selected.has(i);
        const cb = el("input", {type: "checkbox"});
        cb.checked = checked;
        cb.addEventListener("change", () => {
          if (cb.checked) promotionsState.selected.add(i);
          else promotionsState.selected.delete(i);
          debouncedSave();
          debouncedPreview();
        });
        const summary = `${p.from_stage} #${p.rank}`;
        list.appendChild(el("label", {class: "flex items-start gap-3 p-3 border border-gray-800 rounded hover:border-amber-500/40"}, [
          cb,
          el("div", {class: "flex-1"}, [
            el("div", {class: "text-sm font-medium text-gray-100"}, [summary]),
            p.explain ? el("div", {class: "text-xs text-gray-400 mt-0.5"}, [p.explain]) : null,
            el("div", {class: "text-[11px] text-gray-600 mt-0.5"}, [p.promoted_at || ""]),
          ]),
        ]));
      });
      promotionsSection.appendChild(list);
    }

    async function loadPromotions() {
      if (!stage.depends_on_promotions) return;
      try {
        const resp = await fetch(`/api/discovery/sessions/${encodeURIComponent(sessionId)}/promotions`);
        const data = await resp.json().catch(() => ({}));
        promotionsState.items = (data.promotions || []).filter((p) => p.to_stage === stage.id);
        // Default to all selected so the user can run immediately.
        promotionsState.selected = new Set(promotionsState.items.map((_, i) => i));
        renderPromotions();
        debouncedPreview();
      } catch (err) {
        promotionsSection.replaceChildren(el("div", {class: "text-sm text-rose-300"}, [
          `Failed to load promotions: ${err}`,
        ]));
      }
    }
    loadPromotions();

    // ---- Wiring: collect values, persist, refresh preview ----

    function collectValues() {
      const subSignals = {};
      for (const famId in subSignalWidgets) {
        subSignals[famId] = subSignalWidgets[famId].get();
      }
      const selectedPromos = stage.depends_on_promotions
        ? promotionsState.items
            .filter((_, i) => promotionsState.selected.has(i))
            .map((p) => p.yaml_overrides || {})
        : [];
      return {
        enabled_families: widgets.enabled_families.get(),
        sub_signals: subSignals,
        selected_promotion_indices: stage.depends_on_promotions
          ? Array.from(promotionsState.selected.values())
          : [],
        selected_promotions_payload: selectedPromos,
        override_min_trades: widgets.override_min_trades.get(),
        min_trades: widgets.min_trades.get(),
        override_timeframes: widgets.override_timeframes.get(),
        timeframes: widgets.timeframes.get(),
        override_session: widgets.override_session.get(),
        session_hour_from: widgets.session_hour_from.get(),
        session_minute_from: widgets.session_minute_from.get(),
        session_hour_to: widgets.session_hour_to.get(),
        override_tp_sl: widgets.override_tp_sl.get(),
        tp_ticks: widgets.tp_ticks.get(),
        sl_ticks: widgets.sl_ticks.get(),
        // Persisted as raw text so the user's expert tweaks survive reloads.
        expert_overrides_json: expertTextarea.value || "",
      };
    }

    function collectContext() {
      return {
        input_folder: widgets.input_folder.get(),
        output_folder: widgets.output_folder.get(),
        market_data_folder: widgets.market_data_folder.get(),
        recursive: widgets.recursive.get(),
        no_tick_data: widgets.no_tick_data.get(),
      };
    }

    const debouncedSave = debounce(async () => {
      // Persist project context on the session, and form values for this stage.
      const ctxValues = collectContext();
      try {
        await fetch(`/api/discovery/sessions/${encodeURIComponent(sessionId)}`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({context: ctxValues}),
        });
        await fetch(
          `/api/discovery/sessions/${encodeURIComponent(sessionId)}/stages/${encodeURIComponent(stage.id)}/form`,
          {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({values: collectValues()}),
          }
        );
      } catch (err) {
        console.warn("auto-save failed:", err);
      }
    }, 500);

    const debouncedPreview = debounce(refreshPreview, 250);

    async function refreshPreview() {
      const formValues = collectValues();
      const ctxValues = collectContext();
      const enabled = formValues.enabled_families || [];
      const disabled = (stage.enabled_families || []).filter((f) => enabled.indexOf(f) === -1);
      const overrides = buildOverrides(stage, formValues, familiesById);
      const expert = getExpertOverrides();
      if (expert.ok) deepMerge(overrides, expert.overrides);

      const body = {
        instrument_symbol: (sessionDoc && sessionDoc.instrument && sessionDoc.instrument.symbol) || "NQ",
        overrides,
        disabled_families: disabled,
        input_folder: ctxValues.input_folder,
        output_folder: ctxValues.output_folder,
        market_data_folder: ctxValues.market_data_folder,
        recursive: ctxValues.recursive,
        no_tick_data: ctxValues.no_tick_data,
      };

      try {
        const resp = await fetch(
          `/api/discovery/stages/${encodeURIComponent(stage.id)}/preview`,
          {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
          }
        );
        const data = await resp.json().catch(() => ({}));
        previewBox.textContent = data.report_yaml || "(no YAML returned)";
        commandBox.textContent = data.command_preview || "";
        let validation = data.validation || {ok: false, errors: ["No validation data."], warnings: []};
        if (stage.depends_on_promotions && !promotionsState.selected.size) {
          validation = {
            ok: false,
            errors: (validation.errors || []).concat(["Select at least one promoted combo to validate."]),
            warnings: validation.warnings || [],
          };
        }
        renderValidation(validationBox, validation);
        runBtn.disabled = !validation.ok;
      } catch (err) {
        previewBox.textContent = `Failed to generate preview: ${err}`;
        commandBox.textContent = "";
        renderValidation(validationBox, {ok: false, errors: [String(err)], warnings: []});
        runBtn.disabled = true;
      }
    }

    function attachOnChange(widget) {
      widget.onChange(() => {
        debouncedSave();
        debouncedPreview();
      });
    }
    Object.values(widgets).forEach(attachOnChange);

    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true;
      const original = runBtn.textContent;
      runBtn.textContent = "Dispatching…";
      const ctxValues = collectContext();
      const formValues = collectValues();
      const enabled = formValues.enabled_families || [];
      const disabled = (stage.enabled_families || []).filter((f) => enabled.indexOf(f) === -1);
      const overrides = buildOverrides(stage, formValues, familiesById);
      const expert = getExpertOverrides();
      if (!expert.ok) {
        renderValidation(validationBox, {
          ok: false,
          errors: ["Fix the Expert overrides JSON before running."],
          warnings: [],
        });
        runBtn.textContent = original;
        runBtn.disabled = false;
        return;
      }
      deepMerge(overrides, expert.overrides);

      try {
        const resp = await fetch(
          `/api/discovery/sessions/${encodeURIComponent(sessionId)}/runs`,
          {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              stage_id: stage.id,
              instrument_symbol: (sessionDoc && sessionDoc.instrument && sessionDoc.instrument.symbol) || "NQ",
              overrides,
              disabled_families: disabled,
              input_folder: ctxValues.input_folder,
              output_folder: ctxValues.output_folder,
              market_data_folder: ctxValues.market_data_folder,
              recursive: ctxValues.recursive,
              no_tick_data: ctxValues.no_tick_data,
            }),
          }
        );
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          renderValidation(
            validationBox,
            data.validation || {ok: false, errors: [data.error || `HTTP ${resp.status}`], warnings: []}
          );
          runBtn.textContent = original;
          runBtn.disabled = false;
          return;
        }
        if (typeof onRunDispatched === "function" && data.run) {
          onRunDispatched(data.run);
        }
        runBtn.textContent = "Dispatched ✓";

        // Mount the run-watch panel so the user can tail logs and cancel.
        const job = data.job || {};
        if (window.DiscoveryRunWatch && job.id) {
          watchHost.classList.remove("hidden");
          window.DiscoveryRunWatch.mountRunWatch({
            container: watchHost,
            jobId: job.id,
            onTerminal: () => {
              // Re-pull the session so the right-rail run row updates.
              if (typeof onRunDispatched === "function") onRunDispatched(data.run || null);
              runBtn.textContent = original;
              runBtn.disabled = false;
            },
            onViewResults: () => {
              if (typeof deps.onViewResults === "function") deps.onViewResults(data.run);
            },
          });
        } else {
          setTimeout(() => {
            runBtn.textContent = original;
            runBtn.disabled = false;
          }, 1200);
        }
      } catch (err) {
        renderValidation(validationBox, {ok: false, errors: [String(err)], warnings: []});
        runBtn.textContent = original;
        runBtn.disabled = false;
      }
    });

    // Initial preview
    refreshPreview();
  }

  // ---- Helpers ----------------------------------------------------------

  function prettyFamilyLabel(famId) {
    const map = {
      candle: "Candle Patterns",
      ma: "Moving Average",
      orb: "Opening Range Breakout",
      bb: "Bollinger Bands",
      lcr: "Large Candle Region",
      breakout: "Breakout",
      pullback: "Pullback",
      level: "Levels",
      large_candle_excursion: "Large Candle Excursion",
    };
    return map[famId] || famId;
  }

  function firstArrayHead(value) {
    if (Array.isArray(value) && value.length) return value[0];
    if (Number.isFinite(value)) return value;
    return null;
  }

  function debounce(fn, wait) {
    let t = null;
    return function (...args) {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function renderValidation(host, validation) {
    host.replaceChildren();
    if (!validation) return;
    const errors = validation.errors || [];
    const warnings = validation.warnings || [];
    if (validation.ok && !errors.length && !warnings.length) {
      host.appendChild(el("div", {class: "dw-validation-ok"}, ["✓ Configuration valid."]));
      return;
    }
    if (errors.length) {
      host.appendChild(el("ul", {class: "dw-validation-errors mt-1"},
        errors.map((e) => el("li", {}, [e]))));
    }
    if (warnings.length) {
      host.appendChild(el("ul", {class: "dw-validation-warnings mt-2"},
        warnings.map((w) => el("li", {}, [w]))));
    }
  }

  // ---- Public API -------------------------------------------------------

  root.DiscoveryForm = { mountStageForm };
})(window);
