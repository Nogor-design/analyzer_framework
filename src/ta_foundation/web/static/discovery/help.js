/* Discovery onboarding tour, glossary panel, and stage "Stuck?" help.
 *
 * All three features run client-side. They consume:
 *   - window.DiscoveryGlossary (loaded from /api/discovery/glossary)
 *   - the stage payload returned by /api/discovery/stages/<id>
 *   - the family registry passed in via state.familiesById
 *
 * No new server routes are required.
 *
 * Public surface: window.DiscoveryHelp = {
 *   Onboarding: {maybeShow, show},
 *   GlossaryPanel: {toggle, open, close},
 *   StuckHelp: {render(stage, familiesById, glossary) -> HTMLElement},
 * }
 */
(function (root) {
  "use strict";

  const ONBOARDING_KEY = "ta_discovery_onboarded_v1";

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === "class") node.className = attrs[k];
      else if (k === "html") node.innerHTML = attrs[k];
      else if (k.startsWith("on") && typeof attrs[k] === "function") {
        node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
      } else if (attrs[k] !== false && attrs[k] != null) {
        node.setAttribute(k, attrs[k]);
      }
    }
    for (const c of (children || [])) {
      if (c == null || c === false) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  // ---------------------------------------------------------------------
  // Onboarding tour
  // ---------------------------------------------------------------------

  const TOUR_STEPS = [
    {
      title: "Welcome to Strategy Discovery",
      body: (
        "Discovery walks you through a six-stage funnel that finds, ranks, " +
        "and validates trading setups for a single market. You never edit " +
        "YAML — you fill in forms, click run, and promote winners."
      ),
    },
    {
      title: "1. Quick Scan",
      body: (
        "Start here. The Quick Scan tests every signal family on your " +
        "instrument with a single parameter set per family. The output is " +
        "a cross-family ranking. You learn where edge lives in roughly " +
        "three minutes."
      ),
    },
    {
      title: "2-5. Deep Dives",
      body: (
        "Stages 2-5 sweep parameters for whichever family the Quick Scan " +
        "ranked highest. Candle patterns, zones/levels, NY open, " +
        "ORB/momentum — pick the stage that matches what won. You promote " +
        "the best rows to the next stage with one click."
      ),
    },
    {
      title: "6. Validate",
      body: (
        "The final gate. Stage 6 re-runs only the combos you promoted, " +
        "with stricter thresholds and walk-forward IS/OOS validation. " +
        "Trade nothing that hasn't passed Stage 6."
      ),
    },
    {
      title: "Help is everywhere",
      body: (
        "Every parameter has a “?” button with a beginner-friendly " +
        "explanation. Click the Glossary button at the top to browse all " +
        "terms by category. Click the Stuck? button on any stage for " +
        "stage-specific tips."
      ),
    },
  ];

  function buildTour() {
    let stepIdx = 0;
    let dontShow = false;

    const overlay = el("div", {class: "dh-overlay"});
    const panel = el("div", {class: "dh-tour"});

    const titleEl = el("h2", {class: "dh-tour-title"});
    const bodyEl = el("p", {class: "dh-tour-body"});
    const counterEl = el("span", {class: "dh-tour-counter"});

    const prevBtn = el("button", {type: "button", class: "dh-btn dh-btn-ghost"}, ["Back"]);
    const nextBtn = el("button", {type: "button", class: "dh-btn dh-btn-primary"}, ["Next"]);
    const skipBtn = el("button", {type: "button", class: "dh-btn dh-btn-link"}, ["Skip tour"]);
    const remember = el("input", {type: "checkbox"});
    remember.addEventListener("change", () => { dontShow = remember.checked; });

    const dontShowLabel = el("label", {class: "dh-tour-remember"}, [
      remember,
      el("span", {}, ["Don't show this again"]),
    ]);

    function render() {
      const step = TOUR_STEPS[stepIdx];
      titleEl.textContent = step.title;
      bodyEl.textContent = step.body;
      counterEl.textContent = `${stepIdx + 1} / ${TOUR_STEPS.length}`;
      prevBtn.disabled = stepIdx === 0;
      nextBtn.textContent = stepIdx === TOUR_STEPS.length - 1 ? "Get started" : "Next";
    }

    function close() {
      if (dontShow) {
        try { localStorage.setItem(ONBOARDING_KEY, "1"); } catch (e) { /* ignore */ }
      }
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }

    prevBtn.addEventListener("click", () => { if (stepIdx > 0) { stepIdx--; render(); } });
    nextBtn.addEventListener("click", () => {
      if (stepIdx < TOUR_STEPS.length - 1) { stepIdx++; render(); }
      else close();
    });
    skipBtn.addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

    panel.appendChild(el("div", {class: "dh-tour-header"}, [
      titleEl,
      counterEl,
    ]));
    panel.appendChild(bodyEl);
    panel.appendChild(el("div", {class: "dh-tour-footer"}, [
      dontShowLabel,
      el("div", {class: "dh-tour-actions"}, [skipBtn, prevBtn, nextBtn]),
    ]));
    overlay.appendChild(panel);

    render();
    return overlay;
  }

  const Onboarding = {
    show() {
      const overlay = buildTour();
      document.body.appendChild(overlay);
    },
    maybeShow() {
      try {
        if (localStorage.getItem(ONBOARDING_KEY) === "1") return;
      } catch (e) { /* ignore — show anyway */ }
      Onboarding.show();
    },
  };

  // ---------------------------------------------------------------------
  // Glossary slide-in panel
  // ---------------------------------------------------------------------

  let glossaryDom = null;

  function buildGlossaryPanel() {
    const backdrop = el("div", {class: "dh-glossary-backdrop"});
    const panel = el("aside", {class: "dh-glossary-panel"});

    const searchInput = el("input", {
      class: "dw-input",
      type: "text",
      placeholder: "Search terms…",
      autocomplete: "off",
    });

    const closeBtn = el("button", {
      type: "button",
      class: "dh-glossary-close",
      "aria-label": "Close glossary",
    }, ["×"]);

    const body = el("div", {class: "dh-glossary-body"});

    function close() {
      backdrop.classList.remove("is-open");
      panel.classList.remove("is-open");
    }
    backdrop.addEventListener("click", close);
    closeBtn.addEventListener("click", close);

    function render(filter) {
      body.replaceChildren();
      const g = root.DiscoveryGlossary;
      if (!g || !g.categories || !g.terms) {
        body.appendChild(el("div", {class: "dh-glossary-empty"}, [
          "Glossary is still loading. Try again in a moment.",
        ]));
        return;
      }
      const needle = (filter || "").trim().toLowerCase();
      let total = 0;

      for (const cat of g.categories) {
        const termIds = (cat.term_ids || []).filter((tid) => {
          if (!needle) return true;
          const t = g.terms[tid];
          if (!t) return false;
          return (
            tid.toLowerCase().includes(needle) ||
            (t.short_name || "").toLowerCase().includes(needle) ||
            (t.one_line || "").toLowerCase().includes(needle) ||
            (t.details || "").toLowerCase().includes(needle)
          );
        });
        if (!termIds.length) continue;
        total += termIds.length;

        const catNode = el("section", {class: "dh-glossary-cat"}, [
          el("h3", {}, [cat.label]),
          el("p", {class: "dh-glossary-cat-summary"}, [cat.summary || ""]),
        ]);
        const list = el("div", {class: "dh-glossary-terms"});
        for (const tid of termIds) {
          const t = g.terms[tid];
          if (!t) continue;
          list.appendChild(buildTermCard(tid, t));
        }
        catNode.appendChild(list);
        body.appendChild(catNode);
      }

      if (!total) {
        body.appendChild(el("div", {class: "dh-glossary-empty"}, [
          `No glossary entries match “${filter}”.`,
        ]));
      }
    }

    function buildTermCard(tid, t) {
      const card = el("details", {class: "dh-glossary-term"}, [
        el("summary", {}, [
          el("span", {class: "dh-glossary-term-name"}, [t.short_name || tid]),
          el("span", {class: "dh-glossary-term-oneline"}, [t.one_line || ""]),
        ]),
      ]);
      if (t.details) card.appendChild(el("p", {}, [t.details]));
      if (t.formula) {
        card.appendChild(el("p", {}, [el("code", {}, [t.formula])]));
      }
      if (t.why_it_matters) {
        card.appendChild(el("p", {class: "dh-glossary-why"}, [
          el("strong", {}, ["Why it matters: "]),
          t.why_it_matters,
        ]));
      }
      if ((t.see_also || []).length) {
        const links = [];
        for (const ref of t.see_also) {
          const refTerm = (root.DiscoveryGlossary && root.DiscoveryGlossary.terms || {})[ref];
          const a = el("a", {href: "#", class: "dh-glossary-link"}, [
            refTerm ? refTerm.short_name : ref,
          ]);
          a.addEventListener("click", (e) => {
            e.preventDefault();
            // Open the referenced term's <details> and scroll to it.
            const allDetails = body.querySelectorAll(".dh-glossary-term");
            for (const d of allDetails) {
              const name = d.querySelector(".dh-glossary-term-name");
              if (name && name.textContent === (refTerm ? refTerm.short_name : ref)) {
                d.open = true;
                d.scrollIntoView({behavior: "smooth", block: "center"});
                break;
              }
            }
          });
          links.push(a);
        }
        const seeAlso = el("p", {class: "dh-glossary-seealso"}, [
          el("span", {class: "dh-glossary-seealso-label"}, ["See also: "]),
        ]);
        links.forEach((lnk, i) => {
          if (i > 0) seeAlso.appendChild(document.createTextNode(", "));
          seeAlso.appendChild(lnk);
        });
        card.appendChild(seeAlso);
      }
      return card;
    }

    let filterTimer = null;
    searchInput.addEventListener("input", () => {
      if (filterTimer) clearTimeout(filterTimer);
      filterTimer = setTimeout(() => render(searchInput.value), 80);
    });

    panel.appendChild(el("div", {class: "dh-glossary-header"}, [
      el("h2", {}, ["Glossary"]),
      closeBtn,
    ]));
    panel.appendChild(el("div", {class: "dh-glossary-search"}, [searchInput]));
    panel.appendChild(body);

    return {backdrop, panel, render, focusSearch: () => searchInput.focus()};
  }

  function ensureGlossaryDom() {
    if (glossaryDom) return glossaryDom;
    glossaryDom = buildGlossaryPanel();
    document.body.appendChild(glossaryDom.backdrop);
    document.body.appendChild(glossaryDom.panel);
    return glossaryDom;
  }

  const GlossaryPanel = {
    open() {
      const dom = ensureGlossaryDom();
      dom.render("");
      requestAnimationFrame(() => {
        dom.backdrop.classList.add("is-open");
        dom.panel.classList.add("is-open");
        dom.focusSearch();
      });
    },
    close() {
      if (!glossaryDom) return;
      glossaryDom.backdrop.classList.remove("is-open");
      glossaryDom.panel.classList.remove("is-open");
    },
    toggle() {
      const dom = ensureGlossaryDom();
      if (dom.panel.classList.contains("is-open")) GlossaryPanel.close();
      else GlossaryPanel.open();
    },
  };

  // ---------------------------------------------------------------------
  // Stuck? help — stage-specific tips + curated glossary terms
  // ---------------------------------------------------------------------

  // Always-relevant terms across every stage. These are the verdicts and
  // metrics every stage rolls up into.
  const UNIVERSAL_TERMS = [
    "profit_factor", "win_rate", "expectancy", "tier", "is_oos",
  ];

  function termsForStage(stage, familiesById, glossary) {
    if (!glossary || !glossary.terms) return [];
    const seen = new Set();
    const out = [];
    const push = (tid) => {
      if (!tid || seen.has(tid)) return;
      const term = glossary.terms[tid];
      if (!term) return;
      seen.add(tid);
      out.push({id: tid, term});
    };

    // Family-level glossary terms first — most relevant to what the stage tests.
    for (const famId of (stage.enabled_families || [])) {
      const fam = familiesById ? familiesById[famId] : null;
      if (fam && fam.glossary_term) push(fam.glossary_term);
    }
    // Then universal verdict/metric terms.
    UNIVERSAL_TERMS.forEach(push);
    return out.slice(0, 6);
  }

  function buildStuckPanel(stage, familiesById, glossary) {
    const wrap = el("div", {class: "dh-stuck"});

    // Header (collapsible)
    const headerBtn = el("button", {
      type: "button",
      class: "dh-stuck-toggle",
    });
    const arrow = el("span", {class: "dh-stuck-arrow"}, ["▸"]);
    headerBtn.appendChild(arrow);
    headerBtn.appendChild(el("span", {class: "dh-stuck-title"}, ["Stuck?"]));
    headerBtn.appendChild(el("span", {class: "dh-stuck-sub"}, [
      "Stage tips, what to do next, and key terms.",
    ]));

    const body = el("div", {class: "dh-stuck-body"});
    let isOpen = false;

    function applyOpen() {
      wrap.classList.toggle("is-open", isOpen);
      arrow.textContent = isOpen ? "▾" : "▸";
    }
    headerBtn.addEventListener("click", () => { isOpen = !isOpen; applyOpen(); });

    // Stage tips
    if ((stage.sticky_help || []).length) {
      const ul = el("ul", {class: "dh-stuck-list"});
      for (const tip of stage.sticky_help) {
        ul.appendChild(el("li", {}, [tip]));
      }
      body.appendChild(el("section", {class: "dh-stuck-section"}, [
        el("h4", {}, ["Tips for this stage"]),
        ul,
      ]));
    }

    // Where to go next
    const nextIds = stage.next_stage_recommendations || [];
    if (nextIds.length) {
      const recs = el("ul", {class: "dh-stuck-next"});
      for (const nid of nextIds) {
        const link = el("a", {href: "#", class: "dh-stuck-next-link", "data-stage": nid}, [nid]);
        link.addEventListener("click", (e) => {
          e.preventDefault();
          const cb = root.DiscoveryHelp && root.DiscoveryHelp._onStageJump;
          if (typeof cb === "function") cb(nid);
        });
        recs.appendChild(el("li", {}, [link]));
      }
      body.appendChild(el("section", {class: "dh-stuck-section"}, [
        el("h4", {}, ["Where to go after this stage"]),
        recs,
      ]));
    }

    // Curated glossary terms
    const terms = termsForStage(stage, familiesById, glossary);
    if (terms.length) {
      const list = el("div", {class: "dh-stuck-terms"});
      for (const {id, term} of terms) {
        const item = el("details", {class: "dh-stuck-term"}, [
          el("summary", {}, [
            el("span", {class: "dh-stuck-term-name"}, [term.short_name || id]),
            el("span", {class: "dh-stuck-term-oneline"}, [term.one_line || ""]),
          ]),
        ]);
        if (term.details) item.appendChild(el("p", {}, [term.details]));
        list.appendChild(item);
      }
      const more = el("button", {type: "button", class: "dh-btn dh-btn-link"}, ["Browse full glossary →"]);
      more.addEventListener("click", () => GlossaryPanel.open());
      body.appendChild(el("section", {class: "dh-stuck-section"}, [
        el("h4", {}, ["Key terms for this stage"]),
        list,
        more,
      ]));
    }

    wrap.appendChild(headerBtn);
    wrap.appendChild(body);
    applyOpen();
    return wrap;
  }

  const StuckHelp = {
    render(stage, familiesById, glossary) {
      return buildStuckPanel(
        stage || {},
        familiesById || {},
        glossary || root.DiscoveryGlossary || {terms: {}},
      );
    },
  };

  // ---------------------------------------------------------------------
  // Public surface
  // ---------------------------------------------------------------------

  root.DiscoveryHelp = {
    Onboarding,
    GlossaryPanel,
    StuckHelp,
    // Page wires this so Stuck? "go next" links can drive the stage stepper.
    _onStageJump: null,
    setStageJumpHandler(fn) { this._onStageJump = fn; },
  };
})(window);
