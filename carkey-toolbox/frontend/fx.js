// Small visual-effects helpers shared by all tabs: button ripples, toasts, confetti, tab ink.
// No dependencies; every effect is skipped when the OS asks for reduced motion.
"use strict";

const fx = (() => {
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- ripple on every button ----
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled || reduced()) return;
    const r = b.getBoundingClientRect();
    const size = Math.max(r.width, r.height) * 2;
    const ink = document.createElement("span");
    ink.className = "ripple";
    ink.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px`;
    b.appendChild(ink);
    setTimeout(() => ink.remove(), 600);
  });

  // ---- toasts ----
  function toast(message, type = "info", ms = 4200, onClick = null) {
    let host = document.getElementById("toasts");
    if (!host) { host = document.createElement("div"); host.id = "toasts"; document.body.appendChild(host); }
    const icon = { ok: "✅", error: "❌", warn: "⚠️", info: "ℹ️" }[type] || "ℹ️";
    const t = document.createElement("div");
    t.className = `toast ${type}`;
    t.innerHTML = `<span class="t-ico">${icon}</span><span class="t-msg"></span><span class="t-bar"></span>`;
    t.querySelector(".t-msg").textContent = message;
    t.querySelector(".t-bar").style.animationDuration = `${ms}ms`;
    const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), 300); };
    t.addEventListener("click", () => { if (onClick) onClick(); close(); });
    host.appendChild(t);
    setTimeout(close, ms);
  }

  // ---- confetti burst from an element (or the screen centre) ----
  function confetti(fromEl, count = 140) {
    if (reduced()) return;
    const canvas = document.createElement("canvas");
    canvas.className = "confetti";
    canvas.width = innerWidth * devicePixelRatio;
    canvas.height = innerHeight * devicePixelRatio;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    ctx.scale(devicePixelRatio, devicePixelRatio);
    const r = fromEl ? fromEl.getBoundingClientRect() : { left: innerWidth / 2, top: innerHeight / 3, width: 0, height: 0 };
    const ox = r.left + r.width / 2, oy = r.top + r.height / 2;
    const colors = ["#3fbf7f", "#4f9dff", "#8f6bff", "#e8b339", "#ef5b5b", "#ffffff"];
    const parts = Array.from({ length: count }, () => {
      const a = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.2;
      const v = 7 + Math.random() * 9;
      return { x: ox, y: oy, vx: Math.cos(a) * v, vy: Math.sin(a) * v, w: 5 + Math.random() * 6, h: 3 + Math.random() * 5,
        rot: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4, c: colors[(Math.random() * colors.length) | 0], life: 0 };
    });
    const t0 = performance.now();
    (function frame(now) {
      const t = now - t0;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      for (const p of parts) {
        p.vy += 0.28; p.vx *= 0.985; p.vy *= 0.985;
        p.x += p.vx; p.y += p.vy; p.rot += p.vr;
        ctx.save();
        ctx.globalAlpha = Math.max(0, 1 - t / 2600);
        ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      }
      if (t < 2600) requestAnimationFrame(frame); else canvas.remove();
    })(t0);
  }

  // ---- sliding underline under the active tab ----
  function moveInk() {
    const bar = document.getElementById("tabs");
    const active = bar && bar.querySelector(".tab.active");
    let ink = document.getElementById("tab-ink");
    if (!bar || !active) return;
    if (!ink) { ink = document.createElement("span"); ink.id = "tab-ink"; bar.appendChild(ink); }
    ink.style.width = `${active.offsetWidth - 16}px`;
    ink.style.transform = `translateX(${active.offsetLeft + 8}px)`;
  }

  // ---- one-shot shake (re-triggerable) ----
  function shake(el) { if (!el || reduced()) return; el.classList.remove("shake"); void el.offsetWidth; el.classList.add("shake"); }

  window.addEventListener("load", () => {
    document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTimeout(moveInk, 0)));
    window.addEventListener("resize", moveInk);
    setTimeout(moveInk, 50);
  });

  return { toast, confetti, moveInk, shake };
})();
