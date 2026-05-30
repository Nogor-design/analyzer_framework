/* Optimizer shortlist sidebar — shared across Leaderboard, Candidate
 * Results, and Decision Dashboard.
 *
 * Backed by /api/optimizer/sessions/<sid>/shortlist. State lives on the
 * server (see optimizer_shortlist.py); this module is a thin client.
 *
 * Usage from any page:
 *   <script src="/static/optimizer_shortlist.js"></script>
 *   {% include "_shortlist_sidebar.html" %}
 *   <script>OptimizerShortlist.init({ sessionId: "{{ session_id }}" });</script>
 *
 * Public surface:
 *   OptimizerShortlist.init({ sessionId, onChange? })
 *   OptimizerShortlist.add(items, source)    // items: [{stage_id, candidate_id}]
 *   OptimizerShortlist.remove(stage_id, candidate_id)
 *   OptimizerShortlist.clear()
 *   OptimizerShortlist.refresh()
 *   OptimizerShortlist.getState()            // {items, count, finalist_candidate_ids}
 */

(function () {
  "use strict";

  const COLLAPSED_KEY = "ta_foundation.shortlist.collapsed";

  let sessionId = null;
  let state = {
    items: [], count: 0, finalist_candidate_ids: [],
    pending_count: 0, stale_count: 0, promoted_count: 0,
  };
  let onChangeCallbacks = [];
  let initialized = false;

  function endpoint(extra) {
    const base = "/api/optimizer/sessions/" + encodeURIComponent(sessionId) + "/shortlist";
    return extra ? base + extra : base;
  }

  async function request(url, options) {
    const res = await fetch(url, Object.assign({ credentials: "same-origin" }, options || {}));
    if (!res.ok) {
      const txt = await res.text();
      throw new Error("shortlist API " + res.status + ": " + txt);
    }
    return res.json();
  }

  function setState(payload) {
    state = {
      items: Array.isArray(payload.items) ? payload.items : [],
      count: payload.count || 0,
      finalist_candidate_ids: payload.finalist_candidate_ids || [],
      pending_count: payload.pending_count || 0,
      stale_count: payload.stale_count || 0,
      promoted_count: payload.promoted_count || 0,
      updated_at: payload.updated_at || null,
    };
    render();
    onChangeCallbacks.forEach(function (cb) {
      try { cb(state); } catch (e) { console.error("shortlist onChange:", e); }
    });
  }

  function render() {
    const sidebar = document.getElementById("shortlist-sidebar");
    if (!sidebar) return;

    const countEl = document.getElementById("shortlist-count");
    if (countEl) countEl.textContent = String(state.count);

    const pendingEl = document.getElementById("shortlist-pending");
    if (pendingEl) {
      pendingEl.style.display = state.pending_count > 0 ? "" : "none";
      pendingEl.textContent = state.pending_count + " pending";
    }
    const promotedEl = document.getElementById("shortlist-promoted");
    if (promotedEl) {
      promotedEl.style.display = state.promoted_count > 0 ? "" : "none";
      promotedEl.textContent = state.promoted_count + " promoted";
    }
    const staleEl = document.getElementById("shortlist-stale");
    if (staleEl) {
      staleEl.style.display = state.stale_count > 0 ? "" : "none";
      staleEl.textContent = state.stale_count + " stale";
    }
    const promoteBtn = document.getElementById("shortlist-promote-btn");
    if (promoteBtn) {
      promoteBtn.disabled = !(state.pending_count > 0);
    }

    const emptyEl = document.getElementById("shortlist-empty");
    const listEl = document.getElementById("shortlist-items");
    if (!listEl) return;
    listEl.innerHTML = "";

    if (state.count === 0) {
      if (emptyEl) emptyEl.style.display = "";
      return;
    }
    if (emptyEl) emptyEl.style.display = "none";

    state.items.forEach(function (item) {
      listEl.appendChild(renderItem(item));
    });
  }

  function renderItem(item) {
    const li = document.createElement("li");
    li.className = "shortlist-item shortlist-status-" + item.status;

    const head = document.createElement("div");
    head.className = "shortlist-item-head";

    const stagePill = document.createElement("span");
    stagePill.className = "shortlist-pill";
    stagePill.textContent = item.stage_id;
    head.appendChild(stagePill);

    if (item.status === "pending") {
      const tag = document.createElement("span");
      tag.className = "shortlist-tag shortlist-tag-pending";
      tag.textContent = "pending promotion";
      tag.title = "Stage row saved. Click 'Promote pending' to stamp a NinjaTrader fixed-backtest template for it.";
      head.appendChild(tag);
    } else if (item.status === "promoted") {
      const tag = document.createElement("span");
      tag.className = "shortlist-tag shortlist-tag-promoted";
      tag.textContent = item.promoted_run_id || "promoted";
      tag.title = "Promoted to a NinjaTrader fixed-backtest template. Run it in NT, then rebuild the deployment package.";
      head.appendChild(tag);
    } else if (item.status === "stale") {
      const tag = document.createElement("span");
      tag.className = "shortlist-tag shortlist-tag-stale";
      tag.textContent = "stale";
      tag.title = "Row is no longer in the session's results (re-ingest may have changed candidate ids).";
      head.appendChild(tag);
    }

    const remove = document.createElement("button");
    remove.className = "shortlist-remove";
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Remove from shortlist";
    remove.onclick = function () {
      OptimizerShortlist.remove(item.stage_id, item.candidate_id);
    };
    head.appendChild(remove);

    li.appendChild(head);

    const cid = document.createElement("div");
    cid.className = "shortlist-cid";
    cid.textContent = item.candidate_id;
    cid.title = item.candidate_id;
    li.appendChild(cid);

    const kpis = item.kpis || {};
    const kpiLine = document.createElement("div");
    kpiLine.className = "shortlist-kpis";
    kpiLine.textContent = formatKpis(kpis);
    li.appendChild(kpiLine);

    const links = item.links || {};
    if (links.report_url || links.lineage_url || links.stage_results_url) {
      const linkLine = document.createElement("div");
      linkLine.className = "shortlist-links";
      if (links.stage_results_url) {
        linkLine.appendChild(makeLink(links.stage_results_url, "stage"));
      }
      if (links.report_url) {
        linkLine.appendChild(makeLink(links.report_url, "report", true));
      }
      if (links.lineage_url) {
        linkLine.appendChild(makeLink(links.lineage_url, "lineage"));
      }
      li.appendChild(linkLine);
    }
    return li;
  }

  function makeLink(href, text, newTab) {
    const a = document.createElement("a");
    a.href = href;
    a.textContent = text;
    if (newTab) {
      a.target = "_blank";
      a.rel = "noopener";
    }
    return a;
  }

  function formatKpis(k) {
    const parts = [];
    if (k.profit_factor != null) parts.push("PF " + Number(k.profit_factor).toFixed(2));
    if (k.total_net_profit != null) parts.push("Net " + Math.round(Number(k.total_net_profit)));
    if (k.trades != null) parts.push(k.trades + "t");
    return parts.length ? parts.join("  ·  ") : "—";
  }

  function applyCollapsedState() {
    const sidebar = document.getElementById("shortlist-sidebar");
    if (!sidebar) return;
    const collapsed = window.localStorage && window.localStorage.getItem(COLLAPSED_KEY) === "1";
    sidebar.setAttribute("data-collapsed", collapsed ? "true" : "false");
  }

  const OptimizerShortlist = {
    init: function (opts) {
      if (initialized) return;
      opts = opts || {};
      sessionId = String(opts.sessionId || "").trim();
      if (!sessionId) {
        console.warn("OptimizerShortlist.init: no sessionId supplied");
        return;
      }
      if (typeof opts.onChange === "function") {
        onChangeCallbacks.push(opts.onChange);
      }
      initialized = true;
      applyCollapsedState();
      OptimizerShortlist.refresh().catch(function (err) {
        console.error("shortlist initial load failed:", err);
      });
      // If a previous browser session dispatched a promoted run that
      // never finished polling, pick the poll back up on page load.
      OptimizerShortlist._resumePollIfActive().catch(function (err) {
        console.warn("resume promoted-run poll failed:", err);
      });
    },

    _resumePollIfActive: async function () {
      let body;
      try {
        body = await request(endpoint("/promote/status"));
      } catch (err) {
        return;
      }
      const run = body && body.run;
      if (!run || !run.state) return;
      if (run.state === "requested" || run.state === "running") {
        OptimizerShortlist._pollPromotedRun();
      }
    },

    onChange: function (cb) {
      if (typeof cb === "function") onChangeCallbacks.push(cb);
    },

    refresh: async function () {
      if (!initialized) return;
      const payload = await request(endpoint(""));
      setState(payload);
    },

    add: async function (items, source) {
      if (!initialized) throw new Error("shortlist not initialized");
      const normalized = (items || [])
        .map(function (it) {
          return { stage_id: String(it.stage_id || "").trim(), candidate_id: String(it.candidate_id || "").trim() };
        })
        .filter(function (it) { return it.stage_id && it.candidate_id; });
      if (normalized.length === 0) return;
      const payload = await request(endpoint(""), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: normalized, source: source || "ui" }),
      });
      setState(payload);
    },

    remove: async function (stage_id, candidate_id) {
      if (!initialized) return;
      const url = endpoint("/" + encodeURIComponent(stage_id) + "/" + encodeURIComponent(candidate_id));
      const payload = await request(url, { method: "DELETE" });
      setState(payload);
    },

    clear: async function () {
      if (!initialized) return;
      if (state.count > 0 && !window.confirm("Clear " + state.count + " shortlisted row(s)?")) return;
      const payload = await request(endpoint(""), { method: "DELETE" });
      setState(payload);
    },

    promotePending: async function () {
      if (!initialized) return null;
      if (state.pending_count === 0) return null;
      let payload;
      try {
        payload = await request(endpoint("/promote"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dispatch: true }),
        });
      } catch (err) {
        window.alert("Promote & run failed: " + err.message);
        return null;
      }
      if (payload && payload.shortlist) {
        setState(payload.shortlist);
      }
      const result = (payload && payload.result) || {};
      const run = (payload && payload.run) || null;
      if (run && run.error) {
        console.warn("promoted run dispatch:", run.error);
      } else if (run && run.run_id) {
        console.info("promoted run dispatched:", run.run_id, "(" + run.total_templates + " templates)");
        // Kick off background polling so the sidebar reflects the run
        // outcome once NinjaTrader finishes — no second click needed.
        OptimizerShortlist._pollPromotedRun();
      }
      return { result: result, run: run };
    },

    _pollPromotedRun: function () {
      // Idempotent: a second call while a poll is already running is a no-op.
      if (OptimizerShortlist._pollHandle) return;
      const tick = async () => {
        let body;
        try {
          body = await request(endpoint("/promote/status"));
        } catch (err) {
          console.warn("promoted run poll failed:", err.message);
          return;
        }
        const run = body && body.run;
        if (!run) {
          OptimizerShortlist._clearPoll();
          return;
        }
        if (run.state === "complete" || run.state === "failed" || run.state === "cancelled") {
          OptimizerShortlist._clearPoll();
          console.info(
            "promoted run", run.state + ":",
            "completed=" + (run.completed_templates || 0) + "/" + (run.total_templates || 0),
            "reports=" + (run.report_count || 0),
          );
          try { await OptimizerShortlist.refresh(); } catch (e) { /* swallow */ }
          // Surface the outcome wherever the shortlist is embedded.
          onChangeCallbacks.forEach(function (cb) {
            try { cb(state); } catch (e) { console.error("shortlist onChange:", e); }
          });
        }
      };
      // Poll every 10s; runtime is dominated by NT execution (minutes to hours).
      OptimizerShortlist._pollHandle = window.setInterval(tick, 10000);
      // Fire once immediately so the sidebar moves out of "dispatched" promptly.
      tick();
    },

    _clearPoll: function () {
      if (OptimizerShortlist._pollHandle) {
        window.clearInterval(OptimizerShortlist._pollHandle);
        OptimizerShortlist._pollHandle = null;
      }
    },

    cancelPromotedRun: async function () {
      if (!initialized) return null;
      let body;
      try {
        body = await request(endpoint("/promote/cancel"), { method: "POST" });
      } catch (err) {
        window.alert("Cancel failed: " + err.message);
        return null;
      }
      OptimizerShortlist._clearPoll();
      return body && body.run;
    },

    toggle: function () {
      const sidebar = document.getElementById("shortlist-sidebar");
      if (!sidebar) return;
      const next = sidebar.getAttribute("data-collapsed") !== "true";
      sidebar.setAttribute("data-collapsed", next ? "true" : "false");
      if (window.localStorage) {
        window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      }
    },

    getState: function () {
      return Object.assign({}, state);
    },
  };

  window.OptimizerShortlist = OptimizerShortlist;
})();
