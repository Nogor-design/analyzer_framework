/* Discovery run-watch panel.
 *
 * Mounted from discovery_form.js after a "Run this stage" dispatch.
 * Polls /api/jobs/<id>/log incrementally (byte-offset since) and
 * /api/discovery/sessions/<sid>/runs to track terminal status.
 *
 * Renders:
 *   - status pill + elapsed-time counter
 *   - cancel button (disabled once terminal)
 *   - live-tailing <pre> for stdout
 *   - "View results →" button on success that flips the host to the Results tab
 */
(function (root) {
  "use strict";

  const W = root.DiscoveryWidgets;
  if (!W) {
    console.error("DiscoveryWidgets not loaded — widgets.js must come first.");
    return;
  }
  const { el } = W;

  const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

  function statusPillClass(s) {
    return ({
      queued:    "bg-gray-700 text-gray-300",
      running:   "bg-amber-700/60 text-amber-200",
      succeeded: "bg-emerald-800 text-emerald-200",
      failed:    "bg-rose-800 text-rose-200",
      cancelled: "bg-gray-600 text-gray-200",
    })[s] || "bg-gray-700 text-gray-300";
  }

  function fmtElapsed(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
  }

  /**
   * deps: {
   *   container,       HTMLElement
   *   jobId,           string
   *   onTerminal(job), called when status reaches a terminal value
   *   onViewResults(), optional — flip host to Results tab
   * }
   */
  function mountRunWatch(deps) {
    const { container, jobId } = deps;
    container.replaceChildren();

    let offset = 0;
    let pollTimer = null;
    let elapsedTimer = null;
    let stopped = false;
    const startedAt = Date.now();

    const statusPill = el("span", {
      class: `px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wide ${statusPillClass("queued")}`,
    }, ["queued"]);

    const elapsedEl = el("span", {class: "text-xs text-gray-400 ml-2"}, ["0s"]);

    const cancelBtn = el("button", {
      type: "button",
      class: "ml-auto text-xs px-3 py-1 rounded border border-rose-600 text-rose-300 hover:bg-rose-700 hover:text-white",
    }, ["Cancel run"]);

    const viewResultsBtn = el("button", {
      type: "button",
      class: "hidden ml-2 text-xs px-3 py-1 rounded bg-amber-500 text-gray-900 font-semibold hover:bg-amber-400",
    }, ["View results →"]);

    const errorBox = el("div", {class: "hidden mt-2 text-sm text-rose-300"}, []);

    const logPre = el("pre", {
      class: "dw-yaml-preview mt-2",
      style: "max-height: 320px; min-height: 80px;",
    }, ["(waiting for output…)"]);

    container.appendChild(el("div", {class: "flex items-center gap-2 flex-wrap"}, [
      el("span", {class: "text-xs uppercase tracking-wider text-gray-500"}, [`job ${jobId.slice(0, 8)}`]),
      statusPill,
      elapsedEl,
      cancelBtn,
      viewResultsBtn,
    ]));
    container.appendChild(errorBox);
    container.appendChild(logPre);

    let firstChunk = true;
    function appendChunk(text) {
      if (!text) return;
      if (firstChunk) {
        logPre.textContent = "";
        firstChunk = false;
      }
      logPre.appendChild(document.createTextNode(text));
      logPre.scrollTop = logPre.scrollHeight;
    }

    function setStatus(status) {
      statusPill.textContent = status;
      statusPill.className =
        `px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wide ${statusPillClass(status)}`;
    }

    function stopPolling() {
      stopped = true;
      if (pollTimer) clearTimeout(pollTimer);
      if (elapsedTimer) clearInterval(elapsedTimer);
      pollTimer = null;
      elapsedTimer = null;
    }

    cancelBtn.addEventListener("click", async () => {
      cancelBtn.disabled = true;
      cancelBtn.textContent = "Cancelling…";
      try {
        await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {method: "POST"});
      } catch (err) {
        cancelBtn.textContent = "Cancel failed";
      }
    });

    viewResultsBtn.addEventListener("click", () => {
      if (typeof deps.onViewResults === "function") deps.onViewResults();
    });

    elapsedTimer = setInterval(() => {
      elapsedEl.textContent = fmtElapsed(Date.now() - startedAt);
    }, 500);

    async function poll() {
      if (stopped) return;
      try {
        const resp = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/log?since=${offset}`);
        const data = await resp.json().catch(() => ({}));
        if (data.output_since) {
          appendChunk(data.output_since);
          offset = data.length;
        }
        if (data.status) setStatus(data.status);
        if (data.error) {
          errorBox.classList.remove("hidden");
          errorBox.textContent = data.error;
        }
        if (data.status && TERMINAL.has(data.status)) {
          stopPolling();
          cancelBtn.disabled = true;
          cancelBtn.classList.add("opacity-40");
          if (data.status === "succeeded") {
            viewResultsBtn.classList.remove("hidden");
          }
          if (typeof deps.onTerminal === "function") {
            deps.onTerminal({status: data.status, returncode: data.returncode, error: data.error});
          }
          return;
        }
      } catch (err) {
        errorBox.classList.remove("hidden");
        errorBox.textContent = `Log poll failed: ${err}`;
      }
      pollTimer = setTimeout(poll, 1000);
    }

    poll();

    return {
      stop: stopPolling,
    };
  }

  root.DiscoveryRunWatch = { mountRunWatch };
})(window);
