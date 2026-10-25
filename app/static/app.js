"use strict";

// Every example is a query from the shared top-hit list (lakebase-payer-search data/test_top_hit_cases.json).
const EXAMPLES = ["bcbs georgia", "united helth", "uhc", "medicad"];

const PLAN_LABELS = {
  commercial: "Commercial",
  medicare_advantage: "Medicare Advantage",
  medicare: "Medicare",
  medicaid: "Medicaid",
  medicaid_mco: "Medicaid managed care",
  marketplace: "Marketplace",
  military: "Military",
  workers_comp: "Workers' comp",
  auto: "Auto",
  tpa: "Third-party administrator",
};

const DEBOUNCE_MS = 260;
const SEARCHING_MS = 300;
const COPIED_MS = 1600;
const ID_LABEL = "State · Payer ID · synthetic";
const $ = (id) => document.getElementById(id);
const ui = {};
const state = { q: "", page: 0, seq: 0, ctrl: null, timer: 0, slow: 0, rows: [], diagram: null };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function planLabel(plan) { return PLAN_LABELS[plan] || plan; }
function sameName(a, b) { return String(a).trim().toLowerCase() === String(b).trim().toLowerCase(); }

// Owner decision 2026-10-23: the plan name is payer_name without its " - {state}" suffix,
// removed only when the suffix equals the row's own state. No other rule, no new field.
function groupKey(row) {
  const suffix = ` - ${row.state}`;
  return row.payer_name.endsWith(suffix) ? row.payer_name.slice(0, -suffix.length) : row.payer_name;
}

// Groups form only from rows on screen, in the order the search ranked them.
function groupRows(rows) {
  const groups = new Map();
  for (const row of rows) {
    const name = groupKey(row);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(row);
  }
  return [...groups].map(([name, members]) => ({ name, rows: members }));
}

// A count only says what the rows on screen hold.
function countText(group) {
  if (group.rows.length === 1) return group.rows[0].state;
  const states = new Set(group.rows.map((row) => row.state)).size;
  const noun = states === group.rows.length ? "states" : "rows";
  return `${group.rows.length} ${noun} on this page`;
}

function labelled(tag, className, text, field) {
  const node = el(tag, className, text);
  node.dataset.field = field;
  return node;
}

function whyNode(group, className, ctx) {
  const row = group.rows[0];
  const p = el("p", className);
  if (!sameName(row.matched_alias, group.name)) {
    // The highlight marks the matched words once; a repeat of the same alias stays plain.
    if (row.matched_alias !== ctx.lastAlias) p.append("Matched ", labelled("mark", null, row.matched_alias, "matched_alias"));
    else p.append("Matched “", labelled("span", null, row.matched_alias, "matched_alias"), "”");
    p.append(" · ");
  }
  ctx.lastAlias = row.matched_alias;
  p.append(labelled("span", null, planLabel(row.plan_type), "plan_type"));
  return p;
}

function copyButton(payerId) {
  const b = el("button", "copy", "Copy");
  b.type = "button";
  b.setAttribute("aria-label", `Copy payer ID ${payerId}`);
  b.addEventListener("click", () => copy(b, payerId));
  return b;
}

function stateCell(row) {
  const li = el("li", "cell");
  li.append(labelled("span", "st", row.state, "state"), labelled("span", "id", row.payer_id, "payer_id"), copyButton(row.payer_id));
  return li;
}

function renderGroup(group, lead, ctx) {
  const section = el("section", lead ? "group lead" : "group");
  const head = el("div", "group-head");
  head.append(labelled("h3", null, group.name, "payer_name"), el("span", "count", countText(group)));
  const why = whyNode(group, "why", ctx);
  const label = el("p", "states-head label", ID_LABEL);
  label.setAttribute("aria-hidden", "true");
  const list = el("ul", group.rows.length === 1 ? "states one" : "states");
  list.setAttribute("aria-label", `${group.name}: synthetic payer IDs by state`);
  for (const row of group.rows) list.append(stateCell(row));
  if (lead) {
    const band = el("div", "band");
    band.append(head, why);
    section.append(band, label, list);
  } else {
    section.append(head, why, label, list);
  }
  return section;
}

function renderLine(group, ctx) {
  const row = group.rows[0];
  const li = el("li", "line");
  const nm = el("div", "nm");
  nm.append(labelled("p", "n", group.name, "payer_name"), whyNode(group, "why", ctx));
  li.append(nm, labelled("span", "st", row.state, "state"), labelled("span", "pl", planLabel(row.plan_type), "plan_type"),
    labelled("span", "id", row.payer_id, "payer_id"), copyButton(row.payer_id));
  return li;
}

function renderGroups(groups) {
  const ctx = { lastAlias: null };
  const out = [];
  let lines = null;
  groups.forEach((group, i) => {
    if (i === 0 || group.rows.length > 1) {
      lines = null;
      out.push(renderGroup(group, i === 0, ctx));
      return;
    }
    if (!lines) {
      const head = el("div", "lines-head");
      head.setAttribute("aria-hidden", "true");
      for (const t of ["Plan", "State", "Plan type", "Payer ID · synthetic", ""]) head.append(el("span", "label", t));      lines = el("ul", "lines");
      out.push(head, lines);
    }
    lines.append(renderLine(group, ctx));
  });
  ui.groups.replaceChildren(...out);
}

function show(section) {
  for (const id of ["results", "empty", "short", "error"]) ui[id].hidden = id !== section;
}

function clearResults(section) {
  state.q = "";
  state.page = 0;
  state.rows = [];
  ui.groups.replaceChildren();
  show(section);
  lensShow("empty");
}

const REASONS = { 200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
  429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
  504: "Gateway Timeout" };

function statusText(code) { return REASONS[code] ? `${code} ${REASONS[code]}` : String(code); }

function rowsText(n) { return `${n} ${n === 1 ? "row" : "rows"}`; }

// The drawer holds one search: the diagram of the last answer, a search on its way, the failed status, or nothing.
function lensShow(which) {
  ui.lensEmpty.hidden = which !== "empty";
  ui.lensSearching.hidden = which !== "searching";
  ui.lensRun.hidden = which !== "run";
  ui.lensFailed.hidden = which !== "failed";
  if (which !== "run") {
    stopDiagram();
    ui.lensRows.textContent = "Run a search first.";
  }
}

function stopDiagram() {
  const old = ui.lensDiagram.firstElementChild;
  if (old) old.getAnimations({ subtree: true }).forEach((a) => a.cancel());
  if (state.diagram) state.diagram.destroy();
  state.diagram = null;
  ui.lensDiagram.replaceChildren();
  ui.lens.classList.remove("diagram-on");
}

// The definition under hop 4 is lens.definition as the server read it: shown as text, never typed here.
function definitionNode(def) {
  const box = el("details", "pfd-def lens-def");
  box.id = "lens-def";
  const summary = el("summary");
  summary.append(el("span", "pfd-chev"), " Function definition");
  const src = el("p", "def-src");
  src.append("Source: ", el("code", null, def.source.repo), " · ", el("code", null, def.source.path),
    " · commit ", el("code", null, def.source.commit), " · blob ", el("code", null, def.source.blob));
  box.append(summary, src);
  for (const f of def.functions) box.append(el("p", "def-name", f.name), el("pre", "code def-stmt", f.statement));
  return box;
}

// A new root for every answer (the approved component, static/diagram.js), so nothing of an earlier search stays.
function mountDiagram(data, appStatus) {
  const lens = data.lens;
  stopDiagram();
  const root = el("div");
  ui.lensDiagram.append(root);
  state.diagram = window.PayerFinderDiagram.mount(root, {
    query: data.query,
    browser_body: lens.browser_request.body,
    app_body_keys: Object.keys(lens.server_request.body),
    row_count: data.rows.length,
    status: appStatus,
  }, { controls: true });
  ui.lens.classList.add("diagram-on");
  const def = definitionNode(lens.definition);
  root.querySelector('[data-placeholder="ticket 05"]').replaceWith(def);
  root.addEventListener("click", (e) => { if (e.target.closest(".pfd-play")) def.open = false; }, true);
}

// The body as sent, but customer_id by name only: the server sets it, and its value stays off the page.
function argsNodes(args) {
  return Object.keys(args).flatMap((key, i) => [
    ...(i ? [" · "] : []),
    el("code", null, key === "customer_id" ? "customer_id: a label the server sets" : `${key}: ${JSON.stringify(args[key])}`),
  ]);
}

// The only place a time appears: measured by the server for this search (non-negotiable 6). A real Data API search
// is never 0 ms (non-negotiable 4), so a missing or 0 timing shows no number.
function timingText(elapsed) {
  const ms = Math.round(elapsed);
  return Number.isFinite(ms) && ms >= 1 ? ` · ${ms} ms` : "";
}

function renderLens(data, appStatus) {
  const lens = data.lens;
  lensShow("run");
  try {
    mountDiagram(data, appStatus);
  } catch (err) {
    stopDiagram();
    ui.lensDiagram.replaceChildren(el("p", "step-note", "The diagram did not load. The facts below are from this search."));
  }
  ui.lensArgs.replaceChildren(...argsNodes(lens.function.arguments));
  const timing = timingText(data.elapsed_ms);
  ui.lensMeta.textContent = `${statusText(lens.status)} · ${rowsText(data.rows.length)}${timing}`;
  ui.lensNote.hidden = !timing;
  ui.lensRows.textContent = JSON.stringify(data.rows, null, 2);
}

function failedText(res, data) {
  if (res.ok && !data) return "The app server sent an answer that is not JSON.";
  const app = `The app server answered ${statusText(res.status)}.`;
  if (data && data.error === "search_unreachable") return `${app} It could not reach the search service.`;
  if (data && data.upstream_status) return `${app} The search service answered ${statusText(data.upstream_status)}.`;
  return app;
}

function lensFailed(text, title = "This search did not finish.") {
  lensShow("failed");
  ui.lensFailedTitle.textContent = title;
  ui.lensFailedTitle.hidden = !title;
  ui.lensFailedBody.textContent = text;
}

// The results first: nothing in the drawer may keep them off the page.
function render(data, page, appStatus) {
  renderResults(data, page);
  try {
    renderLens(data, appStatus);
  } catch (err) {
    lensFailed("The drawer could not show this search. The results are not affected.", "");
  }
}

function renderResults(data, page) {
  if (page === 1) state.rows = [];
  if (page === 1 && data.rows.length === 0) {
    ui.groups.replaceChildren();
    ui.emptyTitle.textContent = `No payer looks like “${data.query}”.`;
    show("empty");
    return;
  }
  state.rows = state.rows.concat(data.rows);
  state.page = page;
  const groups = groupRows(state.rows);
  renderGroups(groups);
  const plans = `${groups.length} ${groups.length === 1 ? "plan" : "plans"}`;
  const rows = `${state.rows.length} ${state.rows.length === 1 ? "row" : "rows"}`;
  ui.status.textContent = `${plans} for “${data.query}” · ${rows} on this page`;
  ui.more.hidden = data.end;
  ui.end.hidden = !(data.end && page > 1);
  show("results");
}

// Owner decision 2026-10-24: only the front status line changes, and only for a search slower than 300 ms.
// The rows on screen stay; with none on screen, the line shows alone.
function showSearching(q) {
  if (ui.results.hidden) {
    ui.groups.replaceChildren();
    ui.more.hidden = true;
    ui.end.hidden = true;
    show("results");
  }
  ui.status.textContent = `Searching for “${q}”…`;
}

async function run(q, page) {
  const seq = ++state.seq;
  if (state.ctrl) state.ctrl.abort();
  const ctrl = new AbortController();
  state.ctrl = ctrl;
  state.q = q;
  ui.form.classList.add("busy");
  lensShow("searching");
  ui.lensSearching.textContent = `Searching for “${q}”.`;
  clearTimeout(state.slow);
  state.slow = setTimeout(() => { if (seq === state.seq) showSearching(q); }, SEARCHING_MS);
  let res;
  let data;
  try {
    res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q, page }),
      signal: ctrl.signal,
    });
    data = await res.json().catch(() => null);
  } catch (err) {
    if (err.name === "AbortError") return;
    if (seq === state.seq) {
      clearTimeout(state.slow);
      ui.form.classList.remove("busy");
      show("error");
      lensFailed("The app server did not answer.");
    }
    return;
  }
  if (seq !== state.seq) return;
  clearTimeout(state.slow);
  ui.form.classList.remove("busy");
  if (!res.ok || !data) {
    show("error");
    lensFailed(failedText(res, data));
    return;
  }
  render(data, page, res.status);
}

function normalized(value) { return value.replace(/\s+/g, " ").trim(); }

function schedule() {
  clearTimeout(state.timer);
  const q = normalized(ui.q.value);
  if (q.length < 2) {
    state.seq++;
    if (state.ctrl) state.ctrl.abort();
    ui.form.classList.remove("busy");
    clearResults(q.length === 1 ? "short" : null);
    return;
  }
  state.timer = setTimeout(() => run(q, 1), DEBOUNCE_MS);
}

function searchNow(q) {
  clearTimeout(state.timer);
  ui.q.value = q;
  if (normalized(q).length >= 2) run(normalized(q), 1);
}

async function copy(button, text) {
  let label = "Copied";
  try {
    await navigator.clipboard.writeText(text);
    ui.toast.textContent = `Copied ${text}`;
  } catch {
    label = "Blocked";
    ui.toast.textContent = "Copy is blocked in this browser.";
  }
  button.textContent = label;
  button.dataset.done = "";
  clearTimeout(button.timer);
  button.timer = setTimeout(() => { button.textContent = "Copy"; delete button.dataset.done; }, COPIED_MS);
}

function setLens(open) {
  ui.lens.hidden = !open;
  ui.lensToggle.setAttribute("aria-expanded", String(open));
  if (open) ui.lensClose.focus();
  else ui.lensToggle.focus();
}

function init() {
  for (const [key, id] of Object.entries({
    form: "search", q: "q", examples: "examples", results: "results", empty: "empty", short: "short", error: "error",
    emptyTitle: "empty-title", status: "status", groups: "groups",
    more: "more", end: "end", lens: "lens", lensToggle: "lens-toggle", lensClose: "lens-close",
    lensEmpty: "lens-empty", lensRun: "lens-run", lensDiagram: "lens-diagram", lensArgs: "lens-args",
    lensFailed: "lens-failed", lensFailedBody: "lens-failed-body", lensMeta: "lens-meta", lensRows: "lens-rows",
    lensFailedTitle: "lens-failed-title", lensSearching: "lens-searching", lensNote: "lens-note",
    toast: "toast",
  })) ui[key] = $(id);

  EXAMPLES.forEach((q, i) => {
    if (i > 0) ui.examples.append(el("span", null, "·"));
    const chip = el("button", "chip", q);
    chip.type = "button";
    chip.addEventListener("click", () => { searchNow(q); ui.q.focus(); });
    ui.examples.append(chip);
  });

  ui.q.addEventListener("input", schedule);
  ui.form.addEventListener("submit", (e) => { e.preventDefault(); searchNow(ui.q.value); });
  ui.q.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { ui.q.value = ""; schedule(); }
  });
  ui.more.addEventListener("click", () => run(state.q, state.page + 1));
  ui.lensToggle.addEventListener("click", () => setLens(ui.lens.hidden));
  ui.lensClose.addEventListener("click", () => setLens(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== ui.q) { e.preventDefault(); ui.q.focus(); ui.q.select(); }
    if (e.key === "Escape" && !ui.lens.hidden) setLens(false);
  });
  ui.q.focus();
}

document.addEventListener("DOMContentLoaded", init);
