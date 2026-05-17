/* Discovery UI form-widget primitives.
 *
 * Vanilla JS, no build step. Each factory returns:
 *   { el, get(), set(value), onChange(cb) }
 *
 * Specs are plain objects; the only required field is `id`. The rest are
 * optional and described in the JSDoc on each factory.
 *
 * Help-tip integration: pass `glossaryTerm` on any spec; the label will get
 * a "?" affordance whose tooltip is hydrated from window.DiscoveryGlossary
 * (loaded once from /api/discovery/glossary).
 */
(function (root) {
  "use strict";

  // ---- DOM helpers ------------------------------------------------------

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

  // ---- Glossary integration --------------------------------------------

  // window.DiscoveryGlossary is set by the host page after fetching
  // /api/discovery/glossary. Shape: {terms: {id: {short_name, details, ...}}}.
  function lookupTerm(termId) {
    if (!termId) return null;
    const g = root.DiscoveryGlossary;
    if (!g || !g.terms) return null;
    return g.terms[termId] || null;
  }

  function makeHelpTip(termId) {
    if (!termId) return null;
    const tip = el("button", {
      type: "button",
      class: "dw-help-tip",
      "aria-label": "Help",
      title: "What does this mean?",
    }, ["?"]);
    let popover = null;

    const close = () => {
      if (popover && popover.parentNode) popover.parentNode.removeChild(popover);
      popover = null;
      document.removeEventListener("click", onDocClick, true);
    };

    function onDocClick(e) {
      if (!popover) return;
      if (popover.contains(e.target) || tip.contains(e.target)) return;
      close();
    }

    tip.addEventListener("click", (e) => {
      e.stopPropagation();
      if (popover) { close(); return; }
      const term = lookupTerm(termId);
      const body = term
        ? [
            el("h4", {}, [term.short_name || termId]),
            el("div", {}, [term.one_line || ""]),
            term.details ? el("p", {}, [term.details]) : null,
            term.formula ? el("p", {}, [el("code", {}, [term.formula])]) : null,
            term.why_it_matters ? el("p", {class: "text-amber-300/80"}, [term.why_it_matters]) : null,
          ]
        : [el("p", {}, [`Glossary term "${termId}" not loaded yet.`])];
      popover = el("div", {class: "dw-tooltip"}, body);
      const rect = tip.getBoundingClientRect();
      popover.style.top = `${rect.bottom + window.scrollY + 6}px`;
      popover.style.left = `${rect.left + window.scrollX}px`;
      document.body.appendChild(popover);
      document.addEventListener("click", onDocClick, true);
    });
    return tip;
  }

  function makeLabel(text, glossaryTerm) {
    return el("label", {class: "dw-label"}, [
      text,
      makeHelpTip(glossaryTerm),
    ]);
  }

  function makeHelp(text) {
    if (!text) return null;
    return el("div", {class: "dw-helptext"}, [text]);
  }

  // ---- Common subscription helper --------------------------------------

  function subscribable(initialValue) {
    const handlers = [];
    let value = initialValue;
    return {
      get: () => value,
      set: (v) => { value = v; },
      emit: () => { for (const h of handlers) h(value); },
      onChange: (cb) => { if (typeof cb === "function") handlers.push(cb); },
    };
  }

  // ---- Text field ------------------------------------------------------

  /**
   * spec: {id, label, value, placeholder, help, glossaryTerm, type?: "text"|"path"}
   */
  function createTextField(spec) {
    const sub = subscribable(spec.value || "");
    const input = el("input", {
      class: "dw-input",
      type: "text",
      placeholder: spec.placeholder || "",
      value: sub.get(),
    });
    input.addEventListener("input", () => {
      sub.set(input.value);
      sub.emit();
    });
    const node = el("div", {class: "dw-field"}, [
      makeLabel(spec.label || spec.id, spec.glossaryTerm),
      input,
      makeHelp(spec.help),
    ]);
    return {
      el: node,
      get: sub.get,
      set: (v) => { input.value = v == null ? "" : String(v); sub.set(input.value); },
      onChange: sub.onChange,
    };
  }

  // ---- Number field ----------------------------------------------------

  /**
   * spec: {id, label, value, min, max, step, help, glossaryTerm}
   */
  function createNumberField(spec) {
    const sub = subscribable(spec.value == null ? null : Number(spec.value));
    const input = el("input", {
      class: "dw-input dw-narrow",
      type: "number",
      placeholder: spec.placeholder || "",
      value: sub.get() == null ? "" : String(sub.get()),
      min: spec.min != null ? String(spec.min) : false,
      max: spec.max != null ? String(spec.max) : false,
      step: spec.step != null ? String(spec.step) : "any",
    });
    input.addEventListener("input", () => {
      const raw = input.value.trim();
      sub.set(raw === "" ? null : Number(raw));
      sub.emit();
    });
    const node = el("div", {class: "dw-field"}, [
      makeLabel(spec.label || spec.id, spec.glossaryTerm),
      input,
      makeHelp(spec.help),
    ]);
    return {
      el: node,
      get: sub.get,
      set: (v) => { input.value = v == null ? "" : String(v); sub.set(v == null ? null : Number(v)); },
      onChange: sub.onChange,
    };
  }

  // ---- Toggle ----------------------------------------------------------

  /**
   * spec: {id, label, value, help, glossaryTerm}
   */
  function createToggle(spec) {
    const sub = subscribable(Boolean(spec.value));
    const checkbox = el("input", {type: "checkbox"});
    checkbox.checked = sub.get();
    checkbox.addEventListener("change", () => { sub.set(checkbox.checked); sub.emit(); });
    const node = el("div", {class: "dw-field"}, [
      el("label", {class: "dw-toggle"}, [
        checkbox,
        el("span", {class: "text-sm text-gray-200"}, [spec.label || spec.id]),
        makeHelpTip(spec.glossaryTerm),
      ]),
      makeHelp(spec.help),
    ]);
    return {
      el: node,
      get: sub.get,
      set: (v) => { checkbox.checked = Boolean(v); sub.set(checkbox.checked); },
      onChange: sub.onChange,
    };
  }

  // ---- Segmented control -----------------------------------------------

  /**
   * spec: {id, label, value, options: [{value, label}], help, glossaryTerm}
   * options[].value can be any primitive; comparison is strict-equality
   * after JSON-stringify so arrays/objects also work.
   */
  function createSegmented(spec) {
    const options = spec.options || [];
    const sub = subscribable(spec.value);
    const buttons = [];

    function refresh() {
      const cur = JSON.stringify(sub.get());
      buttons.forEach((b, i) => {
        const opt = options[i];
        b.classList.toggle("is-active", JSON.stringify(opt.value) === cur);
      });
    }

    options.forEach((opt) => {
      const btn = el("button", {type: "button"}, [opt.label]);
      btn.addEventListener("click", () => { sub.set(opt.value); refresh(); sub.emit(); });
      buttons.push(btn);
    });

    const segNode = el("div", {class: "dw-segmented"}, buttons);
    refresh();

    const node = el("div", {class: "dw-field"}, [
      makeLabel(spec.label || spec.id, spec.glossaryTerm),
      segNode,
      makeHelp(spec.help),
    ]);
    return {
      el: node,
      get: sub.get,
      set: (v) => { sub.set(v); refresh(); },
      onChange: sub.onChange,
    };
  }

  // ---- Checkbox group --------------------------------------------------

  /**
   * spec: {id, label, options: [{value, label, glossaryTerm?}], value: [],
   *        help, glossaryTerm}
   * Returned value is an array of selected option values.
   */
  function createCheckboxGroup(spec) {
    const options = spec.options || [];
    const sub = subscribable(Array.isArray(spec.value) ? spec.value.slice() : []);
    const inputs = [];

    function syncFromInputs() {
      const out = [];
      options.forEach((opt, i) => { if (inputs[i].checked) out.push(opt.value); });
      sub.set(out);
      sub.emit();
    }

    options.forEach((opt) => {
      const input = el("input", {type: "checkbox"});
      input.checked = sub.get().indexOf(opt.value) !== -1;
      input.addEventListener("change", syncFromInputs);
      inputs.push(input);
    });

    const grid = el("div", {class: "dw-checkgrid"},
      options.map((opt, i) => el("label", {class: "dw-checkrow"}, [
        inputs[i],
        el("span", {class: "text-sm text-gray-200"}, [opt.label]),
        makeHelpTip(opt.glossaryTerm),
      ]))
    );

    const node = el("div", {class: "dw-field"}, [
      makeLabel(spec.label || spec.id, spec.glossaryTerm),
      grid,
      makeHelp(spec.help),
    ]);
    return {
      el: node,
      get: sub.get,
      set: (v) => {
        const arr = Array.isArray(v) ? v.slice() : [];
        sub.set(arr);
        inputs.forEach((input, i) => { input.checked = arr.indexOf(options[i].value) !== -1; });
      },
      onChange: sub.onChange,
    };
  }

  // ---- Number-list field (comma-separated) -----------------------------

  /**
   * spec: {id, label, value: [], help, glossaryTerm, placeholder}
   * Stores an array of numbers. Empty input → []. Whitespace and commas
   * are both accepted as separators.
   */
  function createNumberListField(spec) {
    const initial = Array.isArray(spec.value) ? spec.value.slice() : [];
    const sub = subscribable(initial);
    const input = el("input", {
      class: "dw-input",
      type: "text",
      placeholder: spec.placeholder || "e.g. 1, 5",
      value: initial.join(", "),
    });
    input.addEventListener("input", () => {
      const parts = input.value.split(/[,\s]+/).map((p) => p.trim()).filter(Boolean);
      const nums = [];
      let hadInvalid = false;
      for (const p of parts) {
        const n = Number(p);
        if (Number.isFinite(n)) nums.push(n);
        else hadInvalid = true;
      }
      input.classList.toggle("border-red-500", hadInvalid);
      sub.set(nums);
      sub.emit();
    });
    const node = el("div", {class: "dw-field"}, [
      makeLabel(spec.label || spec.id, spec.glossaryTerm),
      input,
      makeHelp(spec.help),
    ]);
    return {
      el: node,
      get: sub.get,
      set: (v) => {
        const arr = Array.isArray(v) ? v.slice() : [];
        sub.set(arr);
        input.value = arr.join(", ");
      },
      onChange: sub.onChange,
    };
  }

  // ---- Public API ------------------------------------------------------

  root.DiscoveryWidgets = {
    el,
    createTextField,
    createNumberField,
    createToggle,
    createSegmented,
    createCheckboxGroup,
    createNumberListField,
    makeHelpTip,
  };
})(window);
