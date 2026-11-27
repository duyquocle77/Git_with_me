// CI/CD tab: configure paths, pick a pipeline, run it, watch the stage graph + console,
// then open the release folder. Uses el(), api(), $(), inputFor() from app.js and confirmDialog() from lab.js.
"use strict";

const ci = {
  pipelines: [],
  current: null,
  settings: {},       // current path setting values of the selected pipeline
  checks: {},         // NAME -> {exists, must_exist, path}
  run: null,          // {id, pipeline, steps[], releaseDir, state, started, errors[]}
  history: [],
  loaded: false,
  timer: null,
  errCount: 0,
  store: (() => { try { return JSON.parse(localStorage.getItem("ciStore") || "{}"); } catch { return {}; } })(),
};
// ci.store = { __pipeline, params: {pid: {...}}, skip: {pid: [...]}, durations: {pid: {stepId: secs}}, history: [...] }
ci.store.params ||= {}; ci.store.skip ||= {}; ci.store.force ||= {}; ci.store.durations ||= {};
ci.history = ci.store.history || [];

const ART_ICON = { hex: "🧾", bin: "🔐", map: "🗺️", out: "📦", log: "📄", txt: "📄", sal: "📈" };
const MAX_LOG_LINES = 8000;

function ciPersist() { ci.store.history = ci.history.slice(0, 20); try { localStorage.setItem("ciStore", JSON.stringify(ci.store)); } catch {} }
const fmtSecs = (s) => s == null ? "" : s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(2)} MB`;
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const isRunning = () => ci.run && ci.run.state === "running";

// ---------------- load / select ----------------
async function ciLoad() {
  try {
    const res = await api().CicdList();
    ci.pipelines = res.pipelines || [];
    $("#ci-config").textContent = res.config || "";
    $("#ci-config").title = `Config: ${res.config}\nSaved paths: ${res.settings_file}`;
  } catch (e) {
    ci.pipelines = [];
    $("#ci-desc").textContent = "⚠ " + e;
  }
  const sel = $("#ci-pipeline");
  const prev = ci.current?.id || ci.store.__pipeline;
  sel.replaceChildren(...ci.pipelines.map((p) => el("option", { value: p.id, selected: p.id === prev }, p.name)));
  ci.loaded = true;
  ciSelect(sel.value);
  renderHistory();
}

function ciSelect(id) {
  ci.current = ci.pipelines.find((p) => p.id === id) || null;
  ci.store.__pipeline = id; ciPersist();
  const p = ci.current;
  $("#ci-desc").textContent = p ? p.description : "No pipelines configured.";
  $("#ci-title").textContent = p ? p.name : "—";
  ci.settings = Object.fromEntries((p?.settings || []).map((s) => [s.name, s.value]));
  renderPaths();
  const saved = (p && ci.store.params[p.id]) || {};
  $("#ci-params").replaceChildren(...(p ? p.params : []).map((x) => inputFor(x, x.name in saved ? saved[x.name] : x.default)));
  $("#ci-params").querySelectorAll("input,select").forEach((i) => i.addEventListener("input", refreshPreview));
  renderStepToggles();
  $("#ci-open").checked = p ? p.open_release_dir : true;
  if (!isRunning()) { setHero("idle"); renderGraph(stepsFor(p)); $("#ci-result").classList.add("hidden"); }
  refreshPreview();
}

function stepOn(p, s) {
  const skip = new Set(ci.store.skip[p.id] || []), force = new Set(ci.store.force[p.id] || []);
  return (s.enabled || force.has(s.id)) && !skip.has(s.id);
}

function stepsFor(p) {
  return (p ? p.steps : []).map((s) => ({ ...s, status: stepOn(p, s) ? "pending" : "skipped" }));
}

// ---------------- paths ----------------
function renderPaths() {
  const p = ci.current;
  $("#ci-paths").replaceChildren(...(p?.settings || []).map((s) => {
    const input = el("input", { value: ci.settings[s.name] ?? "", spellcheck: "false", "data-name": s.name });
    input.addEventListener("input", () => { ci.settings[s.name] = input.value; onPathsChanged(); });
    const pick = s.type === "string" ? null : el("button", { type: "button", title: "Browse…", onclick: async () => {
      try {
        const cur = ci.checks[s.name]?.path || "";
        const v = s.type === "file" ? await api().PickFile(s.label) : await api().PickFolder(s.label, cur);
        if (v) { input.value = v; ci.settings[s.name] = v; onPathsChanged(); }
      } catch (e) { console.warn(e); }
    } }, s.type === "file" ? "📄" : "📁");
    return el("div", { class: "path-row", id: `path-${s.name}` },
      el("label", {}, el("span", { class: "dot" }), s.label),
      el("div", { class: "path-input" }, input, pick),
      el("div", { class: "path-hint" }, s.help || ""));
  }));
}

const savePaths = debounce(async () => {
  if (!ci.current) return;
  try {
    await api().CicdSaveSettings(ci.current.id, ci.settings);
    const s = $("#ci-saved"); s.textContent = "✓ Paths saved for this PC"; s.style.opacity = 1;
    setTimeout(() => (s.style.opacity = 0), 1500);
  } catch (e) { $("#ci-saved").textContent = "⚠ " + e; }
}, 600);

function onPathsChanged() { savePaths(); refreshPreview(); }

const refreshPreview = debounce(async () => {
  const p = ci.current;
  if (!p) return;
  let res;
  try { res = await api().CicdPreview(p.id, ciParams(), ci.settings); } catch (e) { res = { error: String(e), vars: {}, checks: {} }; }
  ci.checks = res.checks || {};
  let missing = 0;
  for (const s of p.settings) {
    const row = document.getElementById(`path-${s.name}`);
    if (!row) continue;
    const c = ci.checks[s.name];
    const hint = row.querySelector(".path-hint");
    row.classList.remove("ok", "missing", "new");
    if (!c) { hint.textContent = s.help || ""; continue; }
    const resolved = c.path !== ci.settings[s.name] ? `→ ${c.path}` : "";
    if (c.exists) { row.classList.add("ok"); hint.textContent = resolved || s.help || ""; }
    else if (c.must_exist) { row.classList.add("missing"); missing++; hint.textContent = `Not found${resolved ? " " + resolved : ""}`; }
    else { row.classList.add("new"); hint.textContent = `Will be created${resolved ? " " + resolved : ""}`; }
  }
  const badge = $("#ci-paths-badge");
  badge.className = "ci-badge " + (missing ? "bad" : "ok");
  badge.textContent = missing ? `${missing} missing` : "OK";
  const pv = $("#ci-preview");
  if (res.error) {
    pv.className = "ci-preview err"; pv.replaceChildren(`⚠ ${res.error}`);
  } else {
    pv.className = "ci-preview";
    pv.replaceChildren(el("div", { class: "k" }, "📂 Will release to (timestamp set when the run starts)"), el("div", { class: "v" }, res.vars.RELEASE_DIR || "?"));
  }
}, 250);

// ---------------- params & steps ----------------
function ciParams() {
  const out = {};
  for (const x of ci.current?.params || []) {
    const input = $("#ci-params").querySelector(`[name="${x.name}"]`);
    if (!input) continue;
    if (x.type === "bool") out[x.name] = input.checked;
    else if (x.type === "int" || x.type === "float") out[x.name] = input.value === "" ? null : Number(input.value);
    else out[x.name] = input.value;
  }
  return out;
}

function renderStepToggles() {
  const p = ci.current;
  $("#ci-steps-select").replaceChildren(...(p ? p.steps : []).map((s) => {
    const on = stepOn(p, s);
    const cb = el("input", { type: "checkbox", class: "switch", checked: on });
    const row = el("label", { class: "step-tg" + (on ? "" : " off") }, cb, el("span", { class: "ico" }, s.icon), s.name);
    cb.addEventListener("change", () => {
      const skip = new Set(ci.store.skip[p.id] || []), force = new Set(ci.store.force[p.id] || []);
      if (cb.checked) { skip.delete(s.id); if (!s.enabled) force.add(s.id); }
      else { force.delete(s.id); if (s.enabled) skip.add(s.id); }
      ci.store.skip[p.id] = [...skip]; ci.store.force[p.id] = [...force]; ciPersist();
      row.classList.toggle("off", !cb.checked);
      if (!isRunning()) renderGraph(stepsFor(p).map((x) => x.id === s.id ? { ...x, status: cb.checked ? "pending" : "skipped" } : x));
      updateStepsBadge();
    });
    return row;
  }));
  updateStepsBadge();
}

function enabledSteps() {
  return [...$("#ci-steps-select").querySelectorAll(".step-tg")].map((r, i) => ({ step: ci.current.steps[i], on: r.querySelector("input").checked }));
}
function updateStepsBadge() {
  if (!ci.current) return;
  const all = enabledSteps();
  $("#ci-steps-badge").textContent = `${all.filter((x) => x.on).length}/${all.length}`;
}

// ---------------- run ----------------
async function ciRun() {
  const p = ci.current;
  if (!p || isRunning()) return;
  const params = ciParams();
  for (const x of p.params) {
    if (x.required && (params[x.name] === "" || params[x.name] == null)) { flashLog("error", `Missing required parameter: ${x.label}`); return; }
  }
  const missing = p.settings.filter((s) => ci.checks[s.name] && ci.checks[s.name].must_exist && !ci.checks[s.name].exists);
  if (missing.length && !(await confirmDialog(`These paths do not exist:\n\n${missing.map((s) => "• " + s.label).join("\n")}\n\nRun anyway?`))) return;

  const toggles = enabledSteps();
  const skip = toggles.filter((t) => !t.on).map((t) => t.step.id);
  ci.store.params[p.id] = params; ciPersist();

  $("#ci-log").replaceChildren();
  ci.errCount = 0; $("#ci-err-count").textContent = "0";
  ci.run = { id: null, pipeline: p, version: params.VERSION, openWhenDone: $("#ci-open").checked, releaseDir: "", state: "running",
    started: Date.now(), errors: [],
    steps: p.steps.map((s) => ({ ...s, status: skip.includes(s.id) ? "skipped" : "pending" })) };
  $("#ci-result").classList.add("hidden");
  setHero("running");
  renderGraph(ci.run.steps);
  startTimer();
  try {
    const force = toggles.filter((t) => t.on && !t.step.enabled).map((t) => t.step.id);
    ci.run.id = await api().CicdRun(p.id, { ...params, __force: force }, skip, ci.settings);
  } catch (e) {
    ci.run.state = "failed";
    flashLog("error", "✖ " + e);
    setHero("failed");
    stopTimer();
  }
}

function startTimer() { stopTimer(); ci.timer = setInterval(tick, 500); tick(); }
function stopTimer() { clearInterval(ci.timer); ci.timer = null; }
function tick() {
  const r = ci.run;
  if (!r) return;
  const secs = (Date.now() - r.started) / 1000;
  $("#ci-elapsed").textContent = `⏱ ${fmtSecs(secs)}`;
  const run = r.steps.findIndex((s) => s.status === "running");
  if (run >= 0) {
    const meta = document.querySelector(`#node-${run} .node-meta`);
    if (meta) meta.textContent = fmtSecs((Date.now() - r.steps[run].startedAt) / 1000);
  }
  // ETA from previous successful runs
  const dur = ci.store.durations[r.pipeline.id] || {};
  let left = 0, known = true;
  for (const s of r.steps) {
    if (s.status === "pending") { if (dur[s.id] == null) known = false; else left += dur[s.id]; }
    if (s.status === "running" && dur[s.id] != null) left += Math.max(0, dur[s.id] - (Date.now() - s.startedAt) / 1000);
  }
  $("#ci-eta").textContent = r.state === "running" && known && left > 0 ? `≈ ${fmtSecs(left)} left` : "";
  updateProgress();
}

function updateProgress() {
  const r = ci.run;
  if (!r) { $("#ci-progress-bar").style.width = "0"; return; }
  const active = r.steps.filter((s) => s.status !== "skipped");
  const done = active.filter((s) => ["ok", "failed", "cancelled"].includes(s.status)).length;
  const runningPart = active.some((s) => s.status === "running") ? 0.5 : 0;
  $("#ci-progress-bar").style.width = active.length ? `${Math.round(((done + runningPart) / active.length) * 100)}%` : "0";
}

// ---------------- rendering ----------------
function setHero(state) {
  const labels = { idle: "Ready", running: "Running", ok: "Success", failed: "Failed", cancelled: "Stopped" };
  $("#ci-hero").className = `ci-hero ${state}`;
  $("#ci-state").textContent = labels[state] || state;
  const running = state === "running";
  $("#ci-run").classList.toggle("hidden", running);
  $("#ci-stop").classList.toggle("hidden", !running);
  $("#ci-release").textContent = ci.run?.releaseDir || "";
  $("#ci-open-log").classList.toggle("hidden", !ci.run?.releaseDir);
  if (state === "idle") { $("#ci-elapsed").textContent = "⏱ —"; $("#ci-eta").textContent = ""; $("#ci-progress-bar").style.width = "0"; }
}

function renderGraph(steps) {
  const dur = ci.store.durations[ci.current?.id] || {};
  const kids = [];
  steps.forEach((s, i) => {
    if (i > 0) {
      const prev = steps[i - 1];
      const cls = s.status === "running" ? "active" : ["ok", "failed", "cancelled"].includes(s.status) && prev.status !== "pending" ? "done"
        : prev.status === "ok" && s.status === "skipped" ? "done" : "";
      kids.push(el("div", { class: `edge ${cls}` }));
    }
    const badge = { ok: "✓", failed: "✕", cancelled: "■" }[s.status];
    let meta = "";
    if (s.status === "skipped") meta = "skipped";
    else if (s.duration_s != null) meta = fmtSecs(s.duration_s);
    else if (s.status === "pending" && dur[s.id] != null) meta = `~${fmtSecs(dur[s.id])}`;
    kids.push(el("div", { class: `node ${s.status}`, id: `node-${i}`, title: s.message || s.name },
      el("div", { class: "node-dot" }, s.icon || "⚙️", badge ? el("span", { class: "badge" }, badge) : null),
      el("div", { class: "node-name" }, s.name),
      el("div", { class: "node-meta" }, meta)));
  });
  $("#ci-graph").replaceChildren(...kids);
  updateProgress();
}

function logClass(level, msg) {
  if (msg.startsWith("━━")) return "hdr";
  if (msg.startsWith("$ ")) return "cmd";
  if (level === "sys" && msg.startsWith("✔")) return "ok";
  return level;
}

function flashLog(level, message) {
  const box = $("#ci-log");
  box.querySelector(".ci-log-empty")?.remove();
  const cls = logClass(level, message);
  box.append(el("div", { class: cls }, message));
  if (cls === "error") {
    ci.errCount++; $("#ci-err-count").textContent = String(ci.errCount);
    if (ci.run) { ci.run.errors.push(message); if (ci.run.errors.length > 50) ci.run.errors.shift(); }
  }
  while (box.childElementCount > MAX_LOG_LINES) box.firstElementChild.remove();
  if ($("#ci-autoscroll").checked) box.scrollTop = box.scrollHeight;
}

function renderResult(p) {
  const r = ci.run;
  const box = $("#ci-result");
  const secs = (Date.now() - r.started) / 1000;
  const openDir = el("button", { class: "primary", onclick: () => api().OpenPath(r.releaseDir) }, "📂 Open release folder");
  if (p.ok) {
    const groups = {};
    for (const a of p.result.artifacts || []) {
      const [g, ...rest] = a.rel.split("/");
      (groups[rest.length ? g : "/"] ||= []).push({ ...a, name: rest.length ? rest.join("/") : g });
    }
    box.className = "ci-result ok";
    box.replaceChildren(
      el("h3", {}, `✅ Release ${r.version || ""} ready`),
      el("div", { class: "sub" }, `${r.releaseDir}  ·  ${fmtSecs(secs)}`),
      el("div", { class: "row" }, openDir),
      el("div", { class: "art-groups" }, Object.entries(groups).map(([g, files]) => el("div", { class: "art-group" },
        el("h4", {}, g === "/" ? "root" : g),
        files.map((f) => el("button", { class: "art", title: f.path, onclick: () => api().OpenPath(f.path) },
          el("span", {}, ART_ICON[f.name.split(".").pop().toLowerCase()] || "📄"),
          el("span", { class: "nm" }, f.name), el("span", { class: "sz" }, fmtSize(f.size))))))));
  } else {
    const failed = r.steps.find((s) => s.status === "failed");
    box.className = "ci-result failed";
    box.replaceChildren(
      el("h3", {}, p.cancelled ? "⏹ Pipeline stopped" : `❌ Failed${failed ? ` at “${failed.name}”` : ""}`),
      el("div", { class: "sub" }, p.error || ""),
      r.errors.length ? el("pre", {}, r.errors.slice(-8).join("\n")) : null,
      el("div", { class: "row" },
        el("button", { onclick: () => setFilter("error") }, `🔍 Show ${ci.errCount} error line(s)`),
        r.releaseDir ? el("button", { onclick: () => api().OpenPath(joinPath(r.releaseDir, "logs")) }, "📄 Open logs folder") : null));
  }
  box.classList.remove("hidden");
}

function renderHistory() {
  $("#ci-history-badge").textContent = ci.history.length ? String(ci.history.length) : "";
  $("#ci-history").replaceChildren(...(ci.history.length ? ci.history.slice(0, 15).map((h) => el("button", {
    class: `h-item ${h.state}`, title: h.releaseDir || h.error || "", onclick: () => h.releaseDir && api().OpenPath(h.releaseDir),
  }, { ok: "✅", failed: "❌", cancelled: "⏹" }[h.state] || "•", ` ${h.version || ""} · ${h.pipeline}`,
     el("span", { class: "when" }, `${h.at} · ${h.secs}`))) : [el("div", { class: "path-hint" }, "No runs yet.")]));
}

function setFilter(f) {
  document.querySelectorAll("#ci-filter button").forEach((b) => b.classList.toggle("on", b.dataset.filter === f));
  $("#ci-log").className = `ci-log f-${f}`;
}

function joinPath(dir, ...parts) {
  const sep = dir.includes("\\") ? "\\" : "/";
  return [dir.replace(/[\\/]+$/, ""), ...parts].join(sep);
}

// ---------------- events ----------------
const mine = (p) => ci.run && p.run_id && p.run_id.startsWith("ci-") && (!ci.run.id || p.run_id === ci.run.id);

function onCiInfo(p) {
  if (!mine(p)) return;
  ci.run.id = p.run_id;
  ci.run.releaseDir = p.release_dir;
  setHero("running");
}

function onCiStep(p) {
  if (!mine(p)) return;
  const s = ci.run.steps[p.index];
  if (!s) return;
  if (p.status === "running") s.startedAt = Date.now();
  Object.assign(s, { status: p.status, duration_s: p.duration_s, message: p.message });
  if (p.status === "ok" && p.duration_s != null) {
    (ci.store.durations[ci.run.pipeline.id] ||= {})[s.id] = p.duration_s; ciPersist();
  }
  renderGraph(ci.run.steps);
}

function onCiLog(p) { if (mine(p)) flashLog(p.level, p.message); }

function onCiFinished(p) {
  if (!mine(p)) return;
  const r = ci.run;
  r.state = p.ok ? "ok" : p.cancelled ? "cancelled" : "failed";
  for (const s of r.steps) if (s.status === "running" || s.status === "pending") s.status = p.cancelled && s.status === "running" ? "cancelled" : "skipped";
  if (p.result && p.result.release_dir) r.releaseDir = p.result.release_dir;
  stopTimer(); tick();
  renderGraph(r.steps);
  setHero(r.state);
  $("#ci-eta").textContent = "";
  flashLog(p.ok ? "ok" : "error", p.ok ? `✔ Pipeline succeeded in ${fmtSecs((Date.now() - r.started) / 1000)}` : `✖ ${p.error}`);
  renderResult(p);
  ci.history.unshift({ state: r.state, pipeline: r.pipeline.name, version: r.version, releaseDir: r.releaseDir, error: p.error,
    at: new Date().toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }),
    secs: fmtSecs((Date.now() - r.started) / 1000) });
  ciPersist(); renderHistory();
  if (p.ok && r.openWhenDone && r.releaseDir) api().OpenPath(r.releaseDir);
}

// ---------------- init ----------------
window.cicdShow = () => { if (!ci.loaded) ciLoad(); };

window.addEventListener("load", () => {
  $("#ci-pipeline").addEventListener("change", (e) => { if (!isRunning()) ciSelect(e.target.value); });
  $("#ci-run").addEventListener("click", ciRun);
  $("#ci-stop").addEventListener("click", () => ci.run && ci.run.id && api().CancelRun(ci.run.id));
  $("#ci-reload").addEventListener("click", ciLoad);
  $("#ci-open-log").addEventListener("click", () => ci.run && api().OpenPath(joinPath(ci.run.releaseDir, "logs", "pipeline.log")));
  $("#ci-copy").addEventListener("click", () => navigator.clipboard.writeText([...$("#ci-log").children].map((d) => d.textContent).join("\n")));
  document.querySelectorAll("#ci-filter button").forEach((b) => b.addEventListener("click", () => setFilter(b.dataset.filter)));
  const rt = window.runtime;
  rt.EventsOn("cicd.info", onCiInfo);
  rt.EventsOn("cicd.step", onCiStep);
  rt.EventsOn("run.log", onCiLog);
  rt.EventsOn("run.finished", onCiFinished);
  if (!$("#view-cicd").classList.contains("hidden")) ciLoad();
});
