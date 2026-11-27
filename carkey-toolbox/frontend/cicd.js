// CI/CD tab: pick a pipeline, fill parameters, run it, watch steps + log, open the release folder.
// Uses el(), api(), $() from app.js.
"use strict";

const ci = {
  pipelines: [],
  current: null,     // selected pipeline
  run: null,         // {id, pipeline, steps:[{id,name,status,duration_s,message}], releaseDir, state, started}
  history: [],
  loaded: false,
  saved: (() => { try { return JSON.parse(localStorage.getItem("ciParams") || "{}"); } catch { return {}; } })(),
};

const STEP_ICON = { pending: "○", running: "◐", ok: "✔", failed: "✖", skipped: "–", cancelled: "■" };
const MAX_LOG_LINES = 5000;

function ciSave() { try { localStorage.setItem("ciParams", JSON.stringify(ci.saved)); } catch {} }

async function ciLoad() {
  try {
    const res = await api().CicdList();
    ci.pipelines = res.pipelines || [];
    $("#ci-config").textContent = res.config || "";
  } catch (e) {
    ci.pipelines = [];
    $("#ci-desc").textContent = "⚠ " + e;
  }
  const sel = $("#ci-pipeline");
  const prev = ci.current?.id || ci.saved.__pipeline;
  sel.replaceChildren(...ci.pipelines.map((p) => el("option", { value: p.id, selected: p.id === prev }, p.name)));
  ci.loaded = true;
  ciSelect(sel.value);
}

function ciSelect(id) {
  ci.current = ci.pipelines.find((p) => p.id === id) || null;
  ci.saved.__pipeline = id; ciSave();
  const p = ci.current;
  $("#ci-desc").textContent = p ? p.description : "No pipelines configured.";
  const saved = (p && ci.saved[p.id]) || {};
  $("#ci-params").replaceChildren(...(p ? p.params : []).map((x) => inputFor(x, x.name in saved ? saved[x.name] : x.default)));
  const skip = new Set(saved.__skip || []);
  $("#ci-steps-select").replaceChildren(...(p ? p.steps : []).map((s) => el("label", {},
    el("input", { type: "checkbox", "data-step": s.id, checked: s.enabled && !skip.has(s.id) }),
    s.name, el("span", { class: "type" }, s.type))));
  $("#ci-open").checked = p ? p.open_release_dir : true;
  if (!ci.run || ci.run.state !== "running") renderTimeline(p ? p.steps.map((s) => ({ ...s, status: "pending" })) : []);
}

function ciParams() {
  const p = ci.current, out = {};
  for (const x of p.params) {
    const input = $("#ci-params").querySelector(`[name="${x.name}"]`);
    if (!input) continue;
    if (x.type === "bool") out[x.name] = input.checked;
    else if (x.type === "int" || x.type === "float") out[x.name] = input.value === "" ? null : Number(input.value);
    else out[x.name] = input.value;
  }
  return out;
}

async function ciRun() {
  const p = ci.current;
  if (!p || (ci.run && ci.run.state === "running")) return;
  const params = ciParams();
  const skip = [...document.querySelectorAll("#ci-steps-select input")].filter((i) => !i.checked).map((i) => i.dataset.step);
  for (const x of p.params) {
    if (x.required && (params[x.name] === "" || params[x.name] == null)) { ciLogLine("error", `Missing required parameter: ${x.label}`); return; }
  }
  ci.saved[p.id] = { ...params, __skip: skip }; ciSave();

  $("#ci-log").replaceChildren();
  ci.run = { id: null, pipeline: p, openWhenDone: $("#ci-open").checked, releaseDir: "", state: "running", started: new Date(),
    steps: p.steps.map((s) => ({ ...s, status: skip.includes(s.id) || !s.enabled ? "skipped" : "pending" })) };
  renderRunState();
  renderTimeline(ci.run.steps);
  try {
    ci.run.id = await api().CicdRun(p.id, params, skip);
  } catch (e) {
    ci.run.state = "failed";
    ciLogLine("error", "✖ " + e);
    renderRunState();
  }
}

function ciLogLine(level, message) {
  const box = $("#ci-log");
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
  box.append(el("div", { class: level }, message));
  while (box.childElementCount > MAX_LOG_LINES) box.firstElementChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

function renderTimeline(steps) {
  $("#ci-timeline").replaceChildren(...steps.map((s, i) => el("li", { class: s.status, id: `ci-step-${i}` },
    el("span", {}, STEP_ICON[s.status] || "○"),
    el("span", {}, s.name),
    el("span", { class: "dur" }, s.duration_s != null ? `${s.duration_s}s` : ""),
    s.message ? el("span", { class: "msg" }, s.message) : null)));
}

function renderRunState() {
  const r = ci.run;
  const state = r ? r.state : "idle";
  const chip = $("#ci-state");
  chip.className = "chip " + ({ running: "run", ok: "ok", failed: "bad", cancelled: "busy" }[state] || "");
  chip.textContent = state;
  $("#ci-release").textContent = r && r.releaseDir ? r.releaseDir : "";
  const hasDir = !!(r && r.releaseDir);
  $("#ci-open-dir").classList.toggle("hidden", !hasDir);
  $("#ci-open-log").classList.toggle("hidden", !hasDir);
  const running = state === "running";
  $("#ci-run").disabled = running;
  $("#ci-stop").classList.toggle("hidden", !running);
}

function renderHistory() {
  $("#ci-history").replaceChildren(...ci.history.slice(0, 10).map((h) => el("button", {
    class: "ghost", title: h.releaseDir || h.error || "", onclick: () => h.releaseDir && api().OpenPath(h.releaseDir),
  }, { ok: "✔", failed: "✖", cancelled: "■" }[h.state] || "?", ` ${h.name} ${h.version || ""}`,
     el("span", { class: "when" }, `${h.at} · ${h.secs}s`))));
}

function joinPath(dir, ...parts) {
  const sep = dir.includes("\\") ? "\\" : "/";
  return [dir.replace(/[\\/]+$/, ""), ...parts].join(sep);
}

// ---------------- events ----------------
function onCiInfo(p) {
  if (!ci.run || p.run_id !== ci.run.id && ci.run.id) return;
  ci.run.id = p.run_id;
  ci.run.releaseDir = p.release_dir;
  renderRunState();
}

function onCiStep(p) {
  if (!ci.run) return;
  if (ci.run.id && p.run_id !== ci.run.id) return;
  const s = ci.run.steps[p.index];
  if (!s) return;
  Object.assign(s, { status: p.status, duration_s: p.duration_s, message: p.message });
  renderTimeline(ci.run.steps);
}

function onCiLog(p) {
  if (!p.run_id || !p.run_id.startsWith("ci-")) return;
  ciLogLine(p.level, p.message);
}

function onCiFinished(p) {
  if (!p.run_id || !p.run_id.startsWith("ci-") || !ci.run) return;
  const r = ci.run;
  r.state = p.ok ? "ok" : p.cancelled ? "cancelled" : "failed";
  for (const s of r.steps) if (s.status === "running" || s.status === "pending") s.status = p.cancelled ? "cancelled" : "skipped";
  if (p.result && p.result.release_dir) r.releaseDir = p.result.release_dir;
  renderTimeline(r.steps);
  renderRunState();
  const secs = ((new Date() - r.started) / 1000).toFixed(0);
  ciLogLine(p.ok ? "sys" : "error", p.ok ? `✔ Pipeline succeeded in ${secs}s` : `✖ ${p.error}`);
  ci.history.unshift({ state: r.state, name: r.pipeline.name, version: (ci.saved[r.pipeline.id] || {}).VERSION,
    releaseDir: r.releaseDir, error: p.error, at: new Date().toLocaleTimeString(), secs });
  renderHistory();
  if (p.ok && r.openWhenDone && r.releaseDir) api().OpenPath(r.releaseDir);
}

// ---------------- init ----------------
window.cicdShow = () => { if (!ci.loaded) ciLoad(); };

window.addEventListener("load", () => {
  $("#ci-pipeline").addEventListener("change", (e) => ciSelect(e.target.value));
  $("#ci-run").addEventListener("click", ciRun);
  $("#ci-stop").addEventListener("click", () => ci.run && ci.run.id && api().CancelRun(ci.run.id));
  $("#ci-reload").addEventListener("click", ciLoad);
  $("#ci-open-dir").addEventListener("click", () => ci.run && api().OpenPath(ci.run.releaseDir));
  $("#ci-open-log").addEventListener("click", () => ci.run && api().OpenPath(joinPath(ci.run.releaseDir, "logs", "pipeline.log")));
  const rt = window.runtime;
  rt.EventsOn("cicd.info", onCiInfo);
  rt.EventsOn("cicd.step", onCiStep);
  rt.EventsOn("run.log", onCiLog);
  rt.EventsOn("run.finished", onCiFinished);
  renderRunState();
  if (!$("#view-cicd").classList.contains("hidden")) ciLoad();
});
