"use strict";
/* ClaimGuard website core: sign-in, session, shared helpers.
   Each role's screen lives in its own file and registers itself with CG.apps. */
const CG = (() => {
  const apps = {};
  const state = { token: null, role: null, profile: null };
  const $ = s => document.querySelector(s);
  const SVGNS = "http://www.w3.org/2000/svg";
  const ROLE_LABEL = { claimant: "Policyholder", insurer: "Insurer", admin: "Platform admin" };

  // ---------- small helpers ----------
  // Builds DOM elements with textContent only, so claim data can never inject HTML.
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  function svg(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
    kids.flat().forEach(k => k && el.append(k));
    return el;
  }
  const inr = n => "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  const dateFmt = iso => new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  const todayISO = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
  const initials = name => (name || "?").split(" ").map(w => w[0]).slice(0, 2).join("");
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const store = {
    get() { try { return JSON.parse(sessionStorage.getItem("cg-session")); } catch (e) { return null; } },
    set(v) { try { v ? sessionStorage.setItem("cg-session", JSON.stringify(v)) : sessionStorage.removeItem("cg-session"); } catch (e) {} },
  };

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers.Authorization = "Bearer " + state.token;
    const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (res.status === 401 && state.token && !opts.quiet) signOut(true);
    return { ok: res.ok, status: res.status, data };
  }

  function toast(message, kind = "info") {
    const t = h("div", { class: "toast " + kind, role: "status", text: message });
    $("#toasts").append(t);
    setTimeout(() => t.remove(), 4200);
  }

  // ---------- pop-ups ----------
  const openModals = [];            // only the top-most pop-up reacts to Escape
  function modal({ title, body, actions = [], wide = false }) {
    const prev = document.activeElement;
    const me = {};
    openModals.push(me);
    let open = true, resolveClosed;
    const closed = new Promise(r => { resolveClosed = r; });
    const titleEl = h("h2", { text: title });
    const bodyEl = h("div", { class: "modal-body" }, body);
    const actionsEl = h("div", { class: "modal-actions" });
    const box = h("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      titleEl, bodyEl, actionsEl);
    const overlay = h("div", { class: "overlay", onmousedown: e => { if (e.target === overlay) close(); } }, box);
    function onKey(e) { if (e.key === "Escape" && openModals[openModals.length - 1] === me) close(); }
    function close(value) {
      if (!open) return;
      open = false;
      openModals.splice(openModals.indexOf(me), 1);
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      if (prev && prev.focus) prev.focus();
      resolveClosed(value);
    }
    function setActions(list) {
      actionsEl.replaceChildren(...list.map(a => h("button", {
        class: "btn " + (a.kind || ""), type: "button", disabled: a.disabled,
        onclick: () => (a.onClick ? a.onClick(close) : close(a.value)), text: a.label })));
    }
    setActions(actions);
    document.body.append(overlay);
    document.addEventListener("keydown", onKey);
    (box.querySelector("input, select, textarea") || box.querySelector("button")).focus();
    return {
      box, closed, close, setActions, isOpen: () => open,
      setBody: node => bodyEl.replaceChildren(node),
      setTitle: t => { titleEl.textContent = t; box.setAttribute("aria-label", t); },
    };
  }

  function confirm({ title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", kind = "primary", extra }) {
    return new Promise(resolve => {
      const m = modal({ title, body: h("div", null, h("p", { text: message }), extra),
        actions: [{ label: cancelLabel, value: false }, { label: confirmLabel, kind, value: true }] });
      m.closed.then(v => resolve(v === true));
    });
  }

  // ---------- shared page pieces ----------
  function shield() {
    return svg("svg", { viewBox: "0 0 32 32", "aria-hidden": "true", focusable: "false" },
      svg("path", { class: "shield", d: "M16 2.5 28 6.5V15c0 7-5 11.5-12 14.5C9 26.5 4 22 4 15V6.5Z" }),
      svg("polyline", { class: "pulse", points: "8,16 12,16 14.5,11 17.5,21 19.5,16 24,16" }));
  }
  const logo = () => h("a", { class: "logo", href: "/" }, shield(), h("span", { text: "ClaimGuard" }));

  function topbar() {
    const p = state.profile;
    return h("header", { class: "topbar" },
      logo(),
      h("span", { class: "role-badge", text: ROLE_LABEL[state.role] }),
      h("div", { class: "spacer" }),
      h("div", { class: "who" },
        h("span", { class: "avatar", "aria-hidden": "true", text: initials(p.full_name) }),
        h("span", { class: "who-name", text: p.full_name })),
      h("button", { class: "btn ghost", type: "button", onclick: () => signOut(false), text: "Sign out" }));
  }

  // A heartbeat that runs normally, then one irregular beat in red.
  function ecg() {
    const NORMAL = [[10, 60], [18, 52], [26, 60], [40, 60], [44, 66], [50, 14], [56, 78], [62, 60], [76, 60], [88, 50], [100, 60], [110, 60]];
    const ODD = [[10, 60], [18, 52], [26, 60], [36, 60], [40, 70], [47, 4], [54, 108], [62, 66], [72, 72], [84, 66], [96, 60], [110, 60]];
    const BEAT = 110, BEATS = 9;
    let d = "M0,60", odd = "";
    for (let i = 0; i < BEATS; i++) {
      const x = i * BEAT, beat = i === 6 ? ODD : NORMAL;
      const seg = beat.map(([dx, y]) => `L${x + dx},${y}`).join(" ");
      if (i === 6) odd = `M${x},60 ${seg}`;
      d += " " + seg;
    }
    return svg("svg", { class: "ecg", viewBox: `0 0 ${BEATS * BEAT} 120`, "aria-hidden": "true", focusable: "false" },
      svg("path", { class: "ecg-line", d, pathLength: "1" }),
      svg("path", { class: "ecg-odd", d: odd }));
  }

  // ---------- sign-in ----------
  const ROLES = [
    { key: "claimant", name: "Policyholder", hint: "Submit a claim and follow what happens to it." },
    { key: "insurer", name: "Insurer", hint: "Review flagged claims and decide what happens next." },
    { key: "admin", name: "Platform admin", hint: "Watch the system and adjust the fraud rules." },
  ];

  async function signIn(account_id, passcode) {
    const r = await api("/api/login", { method: "POST", body: { account_id, passcode }, quiet: true });
    if (!r.ok) {
      toast(r.data.error === "admin passcode required" ? "That passcode isn't right." : "Couldn't sign in. Try again.", "bad");
      return;
    }
    state.token = r.data.token; state.role = r.data.role; state.profile = r.data.profile;
    store.set({ token: state.token, role: state.role });
    mount();
  }

  async function renderSignIn() {
    document.body.dataset.role = "guest";
    const accountsBox = h("div", { class: "accounts", "aria-live": "polite" });
    const buttons = ROLES.map(r => h("button", { class: "role", type: "button", "data-for": r.key, "aria-pressed": "false",
      onclick: () => choose(r.key) },
      h("span", { class: "role-name", text: r.name }), h("span", { class: "role-hint", text: r.hint })));
    $("#app").replaceChildren(h("div", { class: "signin" },
      logo(),
      h("section", { class: "hero" },
        h("h1", { text: "Healthcare claims, checked before they're paid." }),
        h("p", { text: "ClaimGuard scores each claim for fraud risk in about two seconds, then approves it, queues it for a reviewer, or sends it to manual verification." }),
        ecg()),
      h("h2", { class: "pick", text: "Choose a view" }),
      h("div", { class: "roles" }, buttons),
      accountsBox,
      h("p", { class: "fine", text: "This is a demo. Every account, hospital and claim is synthetic, and signing in needs no password except for the admin account." })));

    const r = await api("/api/demo-accounts", { quiet: true });
    const demo = r.ok ? r.data : null;

    function choose(key) {
      buttons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.for === key)));
      if (!demo) { accountsBox.replaceChildren(h("p", { class: "muted", text: "Couldn't load the demo accounts. Refresh the page." })); return; }
      if (key === "claimant") {
        accountsBox.replaceChildren(h("div", { class: "acct-list" }, demo.claimants.map(a =>
          h("button", { class: "acct", type: "button", onclick: () => signIn(a.account_id) },
            h("strong", { text: a.full_name }), h("span", { text: `${a.city}, ${a.plan} plan` })))));
      } else if (key === "insurer") {
        accountsBox.replaceChildren(h("div", { class: "acct-list" }, demo.insurers.map(a =>
          h("button", { class: "acct", type: "button", onclick: () => signIn(a.account_id) },
            h("strong", { text: a.full_name }), h("span", { text: a.job_title }),
            h("span", { text: `Approves claims up to ${inr(a.approval_limit)}` })))));
      } else {
        const a = demo.admin[0];
        const input = h("input", { id: "passcode", type: "password", autocomplete: "off", placeholder: "Admin passcode" });
        accountsBox.replaceChildren(h("form", { class: "passcode", onsubmit: e => { e.preventDefault(); signIn(a.account_id, input.value); } },
          h("div", { class: "field" }, h("label", { for: "passcode", text: `Sign in as ${a.full_name}` }), input),
          h("button", { class: "btn primary", type: "submit", text: "Sign in" })));
        input.focus();
      }
    }
  }

  function signOut(expired) {
    state.token = state.role = state.profile = null;
    store.set(null);
    renderSignIn();
    if (expired) toast("Your session expired. Sign in again.");
  }

  function mount() {
    document.body.dataset.role = state.role;
    const app = $("#app");
    const impl = apps[state.role];
    if (impl) { impl.mount(app); return; }
    app.replaceChildren(topbar(), h("main", { class: "page" },
      h("div", { class: "panel soon" },
        h("h2", { text: `${ROLE_LABEL[state.role]} console` }),
        h("p", { text: "This view is the next step in the build." }))));
  }

  async function start() {
    const saved = store.get();
    if (saved && saved.token) {
      state.token = saved.token; state.role = saved.role;
      const r = await api("/api/me", { quiet: true });
      if (r.ok) { state.profile = r.data.profile; mount(); return; }
      state.token = state.role = null;
      store.set(null);
    }
    renderSignIn();
  }

  return { h, svg, api, toast, modal, confirm, topbar, inr, dateFmt, todayISO, sleep, apps, state, start, signOut };
})();
