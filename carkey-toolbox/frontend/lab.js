// Lab Control tab: one card per instrument, generated from the Python driver metadata.
// Uses el(), api(), $() from app.js.
"use strict";

const lab = {
  devices: [],
  status: {},           // device id -> last status
  logs: {},             // device id -> [{level, message, file?}]
  running: new Set(),   // "device:action" currently executing
  polling: false,
  timer: null,
  saved: (() => { try { return JSON.parse(localStorage.getItem("labParams") || "{}"); } catch { return {}; } })(),
};

function labSave() { try { localStorage.setItem("labParams", JSON.stringify(lab.saved)); } catch {} }

// ---------------- tabs ----------------
function showView(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  $("#view-tools").classList.toggle("hidden", name !== "tools");
  $("#view-lab").classList.toggle("hidden", name !== "lab");
  $("#tools-sidebar").classList.toggle("hidden", name !== "tools");
  try { localStorage.setItem("view", name); } catch {}
  if (name === "lab") { if (!lab.devices.length) labLoad(); startPolling(); } else stopPolling();
}

// ---------------- data ----------------
async function labLoad(reload = false) {
  try {
    const res = reload ? await api().LabReload() : await api().LabList();
    lab.devices = res.devices || [];
    $("#lab-config").textContent = res.config ? `config: ${res.config}` : "";
    $("#lab-errors").replaceChildren(...(res.errors || []).map((e) => el("div", { class: "err" }, "⚠ " + e)));
  } catch (e) {
    lab.devices = [];
    $("#lab-errors").replaceChildren(el("div", { class: "err" }, "⚠ " + e));
  }
  renderLab();
  pollOnce();
}

async function pollOnce() {
  if (lab.polling || !lab.devices.length) return;
  lab.polling = true;
  try {
    const st = await api().LabStatus(lab.devices.map((d) => d.id));
    for (const [id, s] of Object.entries(st || {})) {
      if (s.busy && lab.status[id]) { lab.status[id] = { ...lab.status[id], busy: true }; continue; }
      lab.status[id] = s;
    }
  } catch (e) {
    console.warn("lab status", e);
  } finally {
    lab.polling = false;
  }
  for (const d of lab.devices) renderStatus(d.id);
}

function startPolling() {
  stopPolling();
  if ($("#lab-poll").checked) lab.timer = setInterval(pollOnce, 2500);
}
function stopPolling() { clearInterval(lab.timer); lab.timer = null; }

function labLog(id, level, message, extra = {}) {
  const arr = (lab.logs[id] ||= []);
  arr.push({ level, message, time: new Date().toLocaleTimeString(), ...extra });
  if (arr.length > 300) arr.splice(0, arr.length - 300);
  renderLog(id);
}

// ---------------- actions ----------------
function confirmDialog(text) {
  return new Promise((resolve) => {
    $("#modal-text").textContent = text;
    $("#modal").classList.remove("hidden");
    const done = (v) => { $("#modal").classList.add("hidden"); resolve(v); };
    $("#modal-ok").onclick = () => done(true);
    $("#modal-cancel").onclick = () => done(false);
  });
}

function readActionParams(dev, act, root) {
  const out = {};
  for (const p of act.params) {
    const input = root.querySelector(`[name="${p.name}"]`);
    if (!input) continue;
    if (p.type === "bool") out[p.name] = input.checked;
    else if (p.type === "int" || p.type === "float") out[p.name] = input.value === "" ? null : Number(input.value);
    else out[p.name] = input.value;
  }
  return out;
}

async function runAction(dev, act, root, btn) {
  const key = `${dev.id}:${act.name}`;
  if (lab.running.has(key)) return;
  if (act.confirm && !(await confirmDialog(act.confirm))) return;
  const params = readActionParams(dev, act, root);
  if (act.params.length) { lab.saved[key] = params; labSave(); }

  lab.running.add(key);
  btn.classList.add("running"); btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "… " + label;
  const t0 = performance.now();
  try {
    const res = await api().LabAction(dev.id, act.name, params);
    const ms = Math.round(performance.now() - t0);
    reportResult(dev.id, act, res, ms);
  } catch (e) {
    labLog(dev.id, "error", `✖ ${act.label}: ${e}`);
  } finally {
    lab.running.delete(key);
    btn.classList.remove("running"); btn.disabled = false; btn.textContent = label;
    pollOnce();
  }
}

function reportResult(id, act, res, ms) {
  const files = [];
  if (res && typeof res === "object") {
    for (const k of ["file", "csv_dir"]) if (res[k]) files.push(res[k]);
  }
  let text;
  if (res == null || res === "") text = "ok";
  else if (typeof res === "string") text = res;
  else text = JSON.stringify(res);
  labLog(id, "ok", `✔ ${act.label} (${ms} ms): ${text}`, { files });
}

// ---------------- rendering ----------------
function paramInput(p, value) {
  if (p.type === "bool") {
    return el("label", { class: "checkbox", title: p.help }, el("input", { type: "checkbox", name: p.name, checked: !!value }), p.label);
  }
  const input = p.type === "choice"
    ? el("select", { name: p.name }, (p.options || []).map((o) => el("option", { value: o, selected: o === value }, o)))
    : el("input", { name: p.name, value: value ?? "", placeholder: p.help || "",
        type: p.type === "int" || p.type === "float" ? "number" : "text", step: p.type === "float" ? "any" : undefined });
  return el("label", { title: p.help }, p.label, input);
}

function renderCard(dev) {
  const groups = {};
  for (const a of dev.actions) (groups[a.group || ""] ||= []).push(a);

  const groupEls = Object.entries(groups).map(([name, acts]) => el("div", { class: "group" },
    name ? el("div", { class: "group-label" }, name) : null,
    acts.map((a) => {
      const saved = lab.saved[`${dev.id}:${a.name}`] || {};
      const box = el("div", { class: "act" + (a.params.length ? " with-params" : "") });
      const btn = el("button", { class: a.style || "", title: a.help || "" }, a.label);
      btn.addEventListener("click", () => runAction(dev, a, box, btn));
      box.append(...a.params.map((p) => paramInput(p, p.name in saved ? saved[p.name] : p.default)), btn);
      return box;
    })));

  return el("div", { class: "card", id: `card-${dev.id}` },
    el("div", { class: "card-head" },
      el("h3", {}, dev.name, el("span", { class: "sub" }, `${dev.title} · ${dev.id}`)),
      el("span", { class: "chip", id: `chip-${dev.id}` }, "…")),
    el("div", { class: "fields", id: `fields-${dev.id}` }),
    el("div", { class: "error", id: `err-${dev.id}` }),
    el("div", { class: "groups" }, groupEls),
    el("div", { class: "card-log", id: `log-${dev.id}` }));
}

function renderLab() {
  const grid = $("#lab-grid");
  grid.replaceChildren(...lab.devices.map(renderCard));
  if (!lab.devices.length) grid.append(el("div", { class: "empty" }, "No devices configured (see python/lab/lab_config.example.json)."));
  for (const d of lab.devices) { renderStatus(d.id); renderLog(d.id); }
}

function renderStatus(id) {
  const s = lab.status[id];
  const chip = document.getElementById(`chip-${id}`);
  if (!chip) return;
  if (!s) { chip.className = "chip"; chip.textContent = "…"; return; }
  chip.className = "chip " + (s.busy ? "busy" : s.connected ? "ok" : "bad") + (s.on ? " on" : "");
  chip.textContent = s.busy ? `${s.state} · busy` : s.state;
  const fields = document.getElementById(`fields-${id}`);
  fields.replaceChildren(...Object.entries(s.fields || {}).flatMap(([k, v]) =>
    [el("span", { class: "k" }, k), el("span", { class: "v" }, String(v))]));
  document.getElementById(`err-${id}`).textContent = s.error || "";
}

function renderLog(id) {
  const box = document.getElementById(`log-${id}`);
  if (!box) return;
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 10;
  box.replaceChildren(...(lab.logs[id] || []).map((l) => el("div", { class: l.level },
    `${l.time}  ${l.message}`,
    (l.files || []).map((f) => [" ", el("a", { title: f, onclick: () => api().OpenPath(f) }, "open")]))));
  if (stick) box.scrollTop = box.scrollHeight;
}

// ---------------- init ----------------
window.addEventListener("load", () => {
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
  $("#btn-lab-reload").addEventListener("click", () => labLoad(true));
  $("#lab-poll").addEventListener("change", startPolling);
  window.runtime.EventsOn("lab.log", (p) => labLog(p.device, p.level, p.message));
  window.runtime.EventsOn("worker.status", () => { lab.devices = []; if (!$("#view-lab").classList.contains("hidden")) labLoad(); });
  let view = "tools";
  try { view = localStorage.getItem("view") || "tools"; } catch {}
  showView(view);
});
