// Minimal frontend: calls Go via window.go.main.App.*, receives events via window.runtime.EventsOn.
"use strict";

const api = () => window.go.main.App;
const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) e.append(c);
  return e;
};

const state = {
  tools: [],
  selected: null,
  runs: new Map(), // run_id -> {tool, status, logs[], progress, result, error}
  activeRun: null,
  lastParams: loadLastParams(),
};

function loadLastParams() {
  try { return JSON.parse(localStorage.getItem("lastParams") || "{}"); } catch { return {}; }
}
function saveLastParams() {
  try { localStorage.setItem("lastParams", JSON.stringify(state.lastParams)); } catch {}
}

// ---------------- Tools ----------------
async function loadTools(reload = false) {
  try {
    state.tools = (reload ? await api().ReloadTools() : await api().ListTools()) || [];
  } catch (e) {
    state.tools = [];
    setStatus(false, String(e));
  }
  renderToolList();
  if (state.selected) {
    const t = state.tools.find((x) => x.name === state.selected.name);
    t ? selectTool(t) : ($("#tool-panel").innerHTML = '<div class="empty">Select a tool on the left.</div>');
  }
}

function renderToolList() {
  const q = $("#search").value.trim().toLowerCase();
  const list = $("#tool-list");
  list.innerHTML = "";
  const groups = {};
  for (const t of state.tools) {
    if (q && !`${t.title} ${t.name} ${t.description} ${t.category}`.toLowerCase().includes(q)) continue;
    (groups[t.category] ||= []).push(t);
  }
  for (const cat of Object.keys(groups).sort()) {
    list.append(el("div", { class: "cat" }, cat));
    for (const t of groups[cat]) {
      list.append(el("button", {
        class: "tool-item" + (state.selected?.name === t.name ? " active" : ""),
        title: t.description, onclick: () => selectTool(t),
      }, t.title));
    }
  }
  if (!list.children.length) list.append(el("div", { class: "empty" }, "No tools found."));
}

function inputFor(p, value) {
  const id = `p-${p.name}`;
  if (p.type === "bool") {
    return el("label", { class: "checkbox", title: p.help },
      el("input", { type: "checkbox", id, name: p.name, checked: !!value }), p.label);
  }
  let input;
  if (p.type === "choice") {
    input = el("select", { id, name: p.name }, (p.options || []).map((o) => el("option", { value: o, selected: o === value }, o)));
  } else {
    input = el("input", {
      id, name: p.name, value: value ?? "", required: p.required,
      type: p.type === "int" || p.type === "float" ? "number" : "text",
      step: p.type === "float" ? "any" : p.type === "int" ? "1" : undefined,
    });
  }
  if (p.type === "file") {
    const pick = el("button", { type: "button", onclick: async () => {
      const f = await api().PickFile(p.label);
      if (f) input.value = f;
    } }, "…");
    input = el("div", { class: "file-row" }, input, pick);
  }
  return el("label", { title: p.help }, p.label + (p.required ? " *" : ""), input);
}

function selectTool(t) {
  state.selected = t;
  renderToolList();
  const saved = state.lastParams[t.name] || {};
  const form = el("form", { class: "params", onsubmit: (e) => { e.preventDefault(); runSelected(); } },
    t.params.map((p) => inputFor(p, p.name in saved ? saved[p.name] : p.default)));
  const panel = $("#tool-panel");
  panel.innerHTML = "";
  panel.append(
    el("h2", {}, t.title),
    el("p", { class: "desc" }, t.description),
    el("div", { class: "source" }, t.source),
    form,
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: runSelected }, "▶ Run"),
      el("button", { class: "ghost", onclick: () => { delete state.lastParams[t.name]; saveLastParams(); selectTool(t); } }, "Reset to defaults")),
  );
}

function readParams(t) {
  const out = {};
  for (const p of t.params) {
    const input = document.getElementById(`p-${p.name}`);
    if (!input) continue;
    if (p.type === "bool") out[p.name] = input.checked;
    else if (p.type === "int" || p.type === "float") out[p.name] = input.value === "" ? null : Number(input.value);
    else out[p.name] = input.value;
  }
  return out;
}

async function runSelected() {
  const t = state.selected;
  if (!t) return;
  const params = readParams(t);
  state.lastParams[t.name] = params;
  saveLastParams();
  try {
    const id = await api().RunTool(t.name, params);
    showRun(id);
  } catch (e) {
    console.error(e);
  }
}

// ---------------- Runs ----------------
function getRun(id, tool) {
  let r = state.runs.get(id);
  if (!r) {
    r = { id, tool: tool || "?", status: "running", logs: [], progress: 0, progressMsg: "", result: null, error: null, started: new Date() };
    state.runs.set(id, r);
  }
  if (tool) r.tool = tool;
  return r;
}

function onRunEvent(kind, p) {
  if (!p || !p.run_id) return;
  const r = getRun(p.run_id, p.tool);
  if (kind === "started") {
    r.logs.push({ level: "sys", message: `▶ ${r.tool} ${JSON.stringify(p.params)}` });
    state.activeRun = r.id;
  } else if (kind === "log") {
    r.logs.push({ level: p.level, message: p.message });
  } else if (kind === "progress") {
    r.progress = p.value; r.progressMsg = p.message || "";
  } else if (kind === "finished") {
    r.status = p.ok ? "ok" : p.cancelled ? "cancel" : "err";
    r.result = p.result ?? null;
    r.error = p.error || null;
    if (p.ok) r.progress = 1;
    const secs = ((new Date() - r.started) / 1000).toFixed(1);
    r.logs.push({ level: p.ok ? "sys" : p.cancelled ? "warn" : "error",
      message: p.ok ? `✔ done in ${secs}s` : `✖ ${p.error}${p.traceback ? "\n" + p.traceback : ""}` });
  }
  scheduleRender();
}

let renderPending = false;
function scheduleRender() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; renderRuns(); });
}

function showRun(id) { state.activeRun = id; renderRuns(); }

function renderRuns() {
  const tabs = $("#run-tabs");
  tabs.innerHTML = "";
  for (const r of [...state.runs.values()].reverse()) {
    tabs.append(el("button", { class: `run-tab ${r.status}${r.id === state.activeRun ? " active" : ""}`, onclick: () => showRun(r.id) },
      el("span", { class: "dot" }), `${r.tool} · ${r.started.toLocaleTimeString()}`));
  }
  const view = $("#run-view");
  const r = state.runs.get(state.activeRun);
  if (!r) { view.innerHTML = '<div class="empty">No runs yet.</div>'; return; }

  const logBox = view.querySelector(".log");
  const stick = !logBox || logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 20;

  view.innerHTML = "";
  const log = el("div", { class: "log" }, r.logs.map((l) => el("div", { class: l.level }, l.message)));
  view.append(
    el("div", { class: "run-meta" },
      el("span", {}, `${r.tool} — ${r.status}`),
      el("div", { class: "progress" }, el("div", { style: `width:${Math.round(r.progress * 100)}%` })),
      el("span", {}, r.progressMsg || `${Math.round(r.progress * 100)}%`),
      r.status === "running"
        ? el("button", { class: "danger", onclick: () => api().CancelRun(r.id) }, "■ Stop")
        : el("button", { onclick: () => api().SaveResult(`${r.tool}-${r.id}.json`, { tool: r.tool, result: r.result, error: r.error, logs: r.logs }) }, "💾 Save"),
      el("button", { class: "ghost", onclick: () => navigator.clipboard.writeText(r.logs.map((l) => l.message).join("\n")) }, "Copy log")),
    log,
  );
  if (r.result !== null) {
    view.append(el("details", { class: "result", open: true }, el("summary", {}, "Result"), el("pre", {}, JSON.stringify(r.result, null, 2))));
  }
  if (stick) log.scrollTop = log.scrollHeight;
}

// ---------------- Worker status ----------------
function setStatus(ok, text) {
  const s = $("#worker-status");
  s.className = "status " + (ok ? "ok" : "bad");
  s.textContent = text;
}

async function refreshStatus() {
  const info = await api().GetWorkerInfo();
  if (info.running && !info.error) setStatus(true, `Python ${info.python} (pid ${info.pid})`);
  else setStatus(false, info.error || "Python worker is not running");
  $("#worker-status").title = `${info.interpreter}\n${info.pythonDir}`;
}

// ---------------- Init ----------------
function init() {
  const rt = window.runtime;
  rt.EventsOn("run.started", (p) => onRunEvent("started", p));
  rt.EventsOn("run.log", (p) => onRunEvent("log", p));
  rt.EventsOn("run.progress", (p) => onRunEvent("progress", p));
  rt.EventsOn("run.finished", (p) => onRunEvent("finished", p));
  rt.EventsOn("worker.log", (p) => console.log("[python]", p.message));
  rt.EventsOn("worker.status", () => refreshStatus());

  $("#search").addEventListener("input", renderToolList);
  $("#btn-reload").addEventListener("click", () => loadTools(true));
  $("#btn-restart").addEventListener("click", async () => {
    setStatus(false, "Restarting…");
    try { await api().RestartWorker(); } catch (e) { setStatus(false, String(e)); }
    await refreshStatus();
    await loadTools();
  });
  $("#btn-clear").addEventListener("click", () => {
    for (const [id, r] of state.runs) if (r.status !== "running") state.runs.delete(id);
    if (!state.runs.has(state.activeRun)) state.activeRun = [...state.runs.keys()].pop() || null;
    renderRuns();
  });

  refreshStatus();
  loadTools();
}

window.addEventListener("load", init);
