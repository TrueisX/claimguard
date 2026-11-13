"use strict";
/* Platform admin console: overview, system health, accounts, rules and model. */
(() => {
  const { h, svg, api, toast, modal, inr, dateFmt, state } = CG;
  const U = () => CG.ui;                       // helpers shared from insurer.js, read when needed
  const pctText = v => v === null || v === undefined ? "n/a" : v + "%";

  function barChart(items, label, tone) {
    const max = Math.max(1, ...items.map(i => i.value));
    return h("div", { class: "bars", role: "img", "aria-label": `${label}: ${items.map(i => `${i.label} ${i.value}`).join(", ")}` },
      items.map(i => h("div", { class: "bar-col" },
        h("div", { class: "bar-val num", text: String(i.value) }),
        h("div", { class: "bar " + (tone ? tone(i) : ""), style: `height:${i.value ? Math.max(3, Math.round(i.value / max * 120)) : 0}px` }),
        h("div", { class: "bar-label", text: i.label }))));
  }

  function hbars(items) {
    const max = Math.max(1, ...items.map(i => i.value));
    return h("div", null, items.map(i => h("div", { class: "hbar" },
      h("span", { text: i.label }), h("div", { class: "track" }, h("div", { class: "fill", style: `width:${i.value / max * 100}%` })),
      h("span", { class: "n num", text: String(i.value) }))));
  }

  function mount(root) {
    const me = state.profile;
    const ui = { tab: "overview", overview: null, health: null, accounts: null, kind: "", q: "", config: null };
    const content = h("div", { class: "main-col" });
    const TABS = [["overview", "Overview"], ["health", "System health"], ["accounts", "Accounts"], ["rules", "Rules & model"], ["graph", "Fraud network"], ["attack", "Attack mode"]];
    const tabEls = TABS.map(([k, label]) => h("button", { class: "tab", type: "button", role: "tab", "data-tab": k, onclick: () => setTab(k), text: label }));

    root.replaceChildren(CG.topbar(), h("main", { class: "page" },
      h("div", { class: "page-head" }, h("div", null, h("h1", { text: "Control room" }), h("p", { class: "muted", text: `${me.full_name}, ${me.job_title}` }))),
      h("div", { class: "tabs", role: "tablist" }, tabEls), content));

    CG.ui.onAccountChanged = () => { if (ui.tab === "accounts") loadAccounts(); };
    setTab("overview");

    function setTab(t) {
      ui.tab = t;
      clearTimeout(ui.timer);
      tabEls.forEach(el => el.setAttribute("aria-selected", String(el.dataset.tab === t)));
      content.replaceChildren(h("p", { class: "muted", text: "Loading..." }));
      ({ overview: loadOverview, health: loadHealth, accounts: loadAccounts, rules: loadRules, graph: loadGraph, attack: loadAttack })[t]();
    }
    const failed = (what, retry) => content.replaceChildren(h("div", { class: "panel empty" }, h("p", { text: `Couldn't load ${what}.` }),
      h("button", { class: "btn", type: "button", onclick: retry, text: "Try again" })));

    // ---------- overview ----------
    async function loadOverview() {
      const [r, cfg] = await Promise.all([api("/api/admin/overview"), ui.config ? { ok: true, data: ui.config } : api("/api/config")]);
      if (!r.ok) return failed("the overview", loadOverview);
      if (cfg.ok) ui.config = cfg.data;
      if (ui.tab !== "overview") return;
      const d = r.data, u = U(), rt = (ui.config && ui.config.routing) || {};
      const lowMax = Number(rt.low_max) * 100 || 30, highMin = Number(rt.high_min) * 100 || 70;
      const stat = (k, n, label) => h("div", { class: "stat " + k }, h("div", { class: "stat-num num", text: n }), h("div", { class: "stat-label", text: label }));
      const status = Object.entries(d.by_status).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: (u.STATUS[k] || [u.pretty(k)])[0], value: v }));
      content.replaceChildren(
        h("div", { class: "stats", style: "margin-bottom:0" },
          stat("accent", String(d.totals.live_claims), `Claims through the pipeline (plus ${d.totals.history_claims.toLocaleString("en-IN")} historical)`),
          stat("ok", pctText(d.rates.auto_approved_pct), "Auto-approved"),
          stat("warn", pctText(d.rates.flagged_pct), "Flagged for a human"),
          stat("bad", inr(d.money.stopped), "Stopped by reviewers")),
        h("div", { class: "two" },
          h("section", { class: "panel" }, h("h2", { text: "Claims per day, last 14 days" }),
            barChart(d.volume.map(v => ({ label: v.date.slice(8), value: v.claims })), "Claims per day")),
          h("section", { class: "panel" }, h("h2", { text: "Risk scores" }),
            h("p", { class: "muted", style: "margin:-6px 0 6px", text: `Claims per tenth of the 0 to 100 range. Amber is manual verification (above ${lowMax}), red is priority (above ${highMin}).` }),
            barChart(d.risk_histogram.map((v, i) => ({ label: String(i * 10), value: v })), "Risk score distribution", i => Number(i.label) + 10 > highMin ? "hot" : Number(i.label) + 10 > lowMax ? "mid" : ""))),
        h("div", { class: "two" },
          h("section", { class: "panel" }, h("h2", { text: "Where claims stand" }), status.length ? hbars(status) : h("p", { class: "empty", text: "No claims yet." })),
          h("section", { class: "panel" }, h("h2", { text: "Reviewers and the model" }),
            h("dl", { class: "rows" },
              u.row("Waiting for a decision", `${d.queue_waiting} claims, ${inr(d.money.waiting)}`),
              u.row("Decided by reviewers", String(d.decisions.decided)),
              u.row("Of those, rejected", pctText(d.rates.reviewer_reject_pct)),
              u.row("Labels for retraining", String(d.decisions.labels_for_retraining)),
              u.row("Accounts", Object.entries(d.accounts).map(([k, n]) => `${n} ${u.pretty(k.toLowerCase())}`).join(", "))))),
        h("section", { class: "panel" }, h("h2", { text: "Audit log: latest decisions" }),
          d.audit.length ? auditTable(d.audit) : h("p", { class: "empty", text: "No reviewer decisions yet." })));
    }

    function auditTable(rows) {
      const u = U();
      const kind = { APPROVE: "ok", REJECT: "bad", ESCALATE: "warn", MANUAL_VERIFICATION: "info" };
      return h("div", { class: "table-wrap" }, h("table", { class: "claims" },
        h("thead", null, h("tr", null, h("th", { text: "When" }), h("th", { text: "Decision" }), h("th", { class: "right", text: "Amount" }), h("th", { text: "By" }), h("th", { class: "hide-sm", text: "Note" }))),
        h("tbody", null, rows.map(r => h("tr", { style: "cursor:default" },
          h("td", { text: u.when(r.reviewed_at) }),
          h("td", null, h("span", { class: "chip " + (kind[r.reviewer_decision] || "muted"), text: u.pretty(r.reviewer_decision || "") })),
          h("td", { class: "right num nowrap", text: inr(r.amount) }),
          h("td", { text: r.reviewer || "" }),
          h("td", { class: "hide-sm muted", text: r.review_notes || "" }))))));
    }

    // ---------- health ----------
    async function loadHealth() {
      const r = await api("/api/admin/health");
      if (!r.ok) return failed("system health", loadHealth);
      if (ui.tab !== "health") return;
      const comps = r.data.components, bad = comps.filter(c => !c.ok).length, warn = comps.filter(c => c.ok && c.warn).length;
      content.replaceChildren(
        h("div", { class: "page-head", style: "margin-bottom:0" },
          h("p", { text: bad ? `${bad} of ${comps.length} checks could not run.` : warn ? `All checks ran. ${warn} need attention.` : `All ${comps.length} checks are healthy.` }),
          h("button", { class: "btn", type: "button", onclick: () => setTab("health"), text: "Check again" })),
        h("div", { class: "health-grid" }, comps.map(c => h("div", { class: "hcard" },
          h("h3", null, h("span", { class: "dot " + (!c.ok ? "bad" : c.warn ? "warn" : ""), "aria-hidden": "true" }), c.name,
            h("span", { class: "chip " + (!c.ok ? "bad" : c.warn ? "warn" : "ok"), text: !c.ok ? "Unavailable" : c.warn ? "Attention" : "Healthy" })),
          h("p", { text: c.detail || "" })))),
        h("p", { class: "muted", style: "font-size:.875rem", text: `Checked ${U().when(r.data.checked_at)}. "Unavailable" usually means this lab role is not allowed to read that service, not that the service is down.` }));
    }

    // ---------- accounts ----------
    async function loadAccounts() {
      const qs = new URLSearchParams({ kind: ui.kind, q: ui.q }).toString();
      const r = await api("/api/admin/accounts?" + qs);
      if (!r.ok) return failed("accounts", loadAccounts);
      if (ui.tab !== "accounts") return;
      ui.accounts = r.data;
      draw();
    }
    function draw() {
      const d = ui.accounts, u = U();
      const kinds = [["", "All"], ["CLAIMANT", "Policyholders"], ["HOSPITAL", "Hospitals"], ["INSURER", "Insurers"], ["ADMIN", "Admin"]];
      const total = Object.values(d.counts).reduce((a, b) => a + b, 0);
      const search = h("input", { type: "search", value: ui.q, placeholder: "Search by ID, name or city", "aria-label": "Search accounts" });
      let timer;
      search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { ui.q = search.value.trim(); loadAccounts(); }, 300); });
      const filters = kinds.map(([k, label]) => h("button", { class: "filter", type: "button", "aria-pressed": String(k === ui.kind),
        onclick: () => { ui.kind = k; loadAccounts(); }, text: `${label} ${k ? (d.counts[k] || 0) : total}` }));
      content.replaceChildren(h("section", { class: "panel" },
        h("div", { class: "filters", role: "group", "aria-label": "Account type" }, filters),
        h("div", { class: "search-row" }, search),
        d.accounts.length ? h("div", { class: "table-wrap" }, h("table", { class: "claims" },
          h("thead", null, h("tr", null, h("th", { text: "ID" }), h("th", { text: "Name" }), h("th", { class: "hide-sm", text: "Details" }), h("th", { text: "Status" }))),
          h("tbody", null, d.accounts.map(a => h("tr", { tabindex: "0", onclick: () => u.openAccount(a.account_id), onkeydown: e => { if (e.key === "Enter") u.openAccount(a.account_id); } },
            h("td", { class: "nowrap", text: a.account_id }), h("td", { text: a.full_name || a.name }),
            h("td", { class: "hide-sm muted", text: [a.job_title, a.plan && a.plan + " plan", a.city].filter(Boolean).join(" · ") }),
            h("td", null, accountChip(a))))))) : h("p", { class: "empty", text: "No accounts match." }),
        d.total > d.accounts.length ? h("p", { class: "muted", text: `Showing the first ${d.accounts.length} of ${d.total}. Search to narrow the list.` }) : null));
      if (document.activeElement === document.body && ui.q) search.focus();
    }
    function accountChip(a) {
      if (a.kind === "CLAIMANT") return h("span", { class: "chip " + (a.policy_status === "Suspended" ? "bad" : "ok"), text: a.policy_status || "Active" });
      if (a.kind === "HOSPITAL") return h("span", { class: "chip " + (a.network_status === "In-network" ? "ok" : "warn"), text: a.network_status || "Unknown" });
      return h("span", { class: "chip muted", text: U().pretty(a.kind.toLowerCase()) });
    }



    // ---------- fraud network ----------
    async function loadGraph() {
      const r = await api("/api/admin/graph");
      if (!r.ok) return failed("the fraud network", loadGraph);
      if (ui.tab !== "graph") return;
      const d = r.data, u = U();
      const sentence = f => f.type === "hub"
        ? `${f.label} has flagged claims from ${f.patients} different policyholders, ${inr(f.amount)} in total. One hospital behind many suspicious patients can mean a billing ring.`
        : f.type === "hopper"
          ? `${f.label} has flagged claims at ${f.hospitals} different hospitals, ${inr(f.amount)} in total. Moving between hospitals can hide a pattern.`
          : `${f.label} has ${f.claims} flagged claims at ${f.hospital}, ${inr(f.amount)} in total. Repeated bills for the same hospital and patient look like duplicate billing.`;
      content.replaceChildren(
        h("section", { class: "panel" },
          h("div", { class: "panel-head" }, h("h2", { text: "Who is connected to whom" }),
            h("button", { class: "btn", type: "button", onclick: () => setTab("graph"), text: "Refresh" })),
          h("p", { class: "muted", style: "margin:4px 0 12px", text: `Every line is a policyholder and a hospital linked by at least one claim scored ${Math.round(d.threshold * 100)} or above. Only claims that went through the live pipeline are drawn, not the historical data.` }),
          d.nodes.length ? graphView(d) : h("p", { class: "empty", text: "No flagged claims right now. Launch Attack mode to watch a ring appear here." })),
        h("section", { class: "panel" }, h("h2", { text: "What stands out" }),
          d.findings.length ? h("ul", { class: "facts", style: "margin-top:10px" }, d.findings.map(f => h("li", { class: "flag", text: sentence(f) })))
            : h("p", { class: "muted", style: "margin-top:8px", text: d.nodes.length ? "No repeated bills, hub hospitals or hopping policyholders in the flagged claims." : "Nothing to show yet." })));
    }

    // Small force layout, run once and drawn still. A seeded random start makes it look the same every time.
    function layout(nodes, edges, W, H) {
      let seed = 7;
      const rnd = () => { seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
      const byId = new Map(nodes.map(n => [n.id, n]));
      nodes.forEach(n => { n.x = W / 2 + (rnd() - 0.5) * W * 0.7; n.y = H / 2 + (rnd() - 0.5) * H * 0.7; n.vx = 0; n.vy = 0; });
      const links = edges.map(e => [byId.get(e.source), byId.get(e.target)]);
      for (let step = 0; step < 320; step++) {
        const cool = 1 - step / 320;
        for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || 0.01;
          const f = 5200 / d2, d = Math.sqrt(d2);
          dx = dx / d * f; dy = dy / d * f;
          a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
        }
        for (const [a, b] of links) {
          const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - 95) * 0.04;
          a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
        }
        for (const n of nodes) {
          n.vx += (W / 2 - n.x) * 0.004; n.vy += (H / 2 - n.y) * 0.004;
          n.x += Math.max(-14, Math.min(14, n.vx)) * cool; n.y += Math.max(-14, Math.min(14, n.vy)) * cool;
          n.vx *= 0.6; n.vy *= 0.6;
        }
      }
      const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      const sx = (W - 140) / Math.max(1, x1 - x0), sy = (H - 110) / Math.max(1, y1 - y0), k = Math.min(sx, sy, 2.2);
      nodes.forEach(n => { n.x = W / 2 + (n.x - (x0 + x1) / 2) * k; n.y = H / 2 + (n.y - (y0 + y1) / 2) * k; });
    }

    function graphView(d) {
      const u = U(), W = 960, H = 560;
      layout(d.nodes, d.edges, W, H);
      const byId = new Map(d.nodes.map(n => [n.id, n]));
      const tone = risk => risk >= d.high ? "var(--bad)" : "#D9930D";
      const edgeEls = d.edges.map(e => {
        const a = byId.get(e.source), b = byId.get(e.target);
        return svg("g", null,
          svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: tone(e.max_risk), "stroke-width": 1.5 + Math.min(e.claims, 5) * 1.3, "stroke-linecap": "round", opacity: ".75" }),
          e.claims > 1 ? svg("text", { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 5, "text-anchor": "middle", class: "g-edge-label" }, `×${e.claims}`) : null);
      });
      const open = n => u.openAccount(n.id);
      const nodeEls = d.nodes.map(n => {
        const hosp = n.kind === "HOSPITAL";
        const shape = hosp
          ? svg("rect", { x: n.x - 14, y: n.y - 14, width: 28, height: 28, rx: 6, fill: "var(--accent)" })
          : svg("circle", { cx: n.x, cy: n.y, r: 7 + Math.min(n.claims, 4) * 2, fill: tone(n.max_risk), stroke: "#fff", "stroke-width": 2 });
        const label = hosp ? n.id : n.id;
        const g = svg("g", { class: "g-node", tabindex: "0", role: "button", "aria-label": `${hosp ? "Hospital" : "Policyholder"} ${n.label}, ${n.claims} flagged ${n.claims === 1 ? "claim" : "claims"}` },
          svg("title", null, `${n.label}: ${n.claims} flagged ${n.claims === 1 ? "claim" : "claims"}, ${inr(n.amount)}`),
          shape,
          svg("text", { x: n.x, y: n.y + (hosp ? 30 : 24 + Math.min(n.claims, 4) * 2), "text-anchor": "middle", class: "g-label" + (hosp ? " g-hosp" : "") }, n.id));
        g.addEventListener("click", () => open(n));
        g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(n); } });
        return g;
      });
      const legend = h("div", { class: "g-legend" },
        h("span", null, h("i", { class: "sq" }), " Hospital"), h("span", null, h("i", { class: "ci bad" }), " Policyholder, priority-level risk"),
        h("span", null, h("i", { class: "ci mid" }), " Policyholder, manual-verification risk"), h("span", { text: "Thicker line and × number mean more claims between the pair. Click a node for details." }));
      return h("div", null,
        h("div", { class: "graph-wrap" }, svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "graph", role: "group", "aria-label": "Network of flagged claims between policyholders and hospitals" }, edgeEls, nodeEls)),
        legend);
    }

    // ---------- attack mode ----------
    const PATTERNS = [
      ["Upcoding", "A fever billed as major surgery, three times, at three different hospitals."],
      ["Duplicate billing", "One policyholder billed three identical X-ray claims at the same hospital on the same day."],
      ["Provider burst", "One hospital suddenly bills three high MRI claims for back pain, for three different patients."]];

    async function loadAttack() {
      const r = await api("/api/admin/attack");
      if (!r.ok) return failed("attack mode", loadAttack);
      if (ui.tab !== "attack") return;
      drawAttack(r.data);
      if (r.data.active && !r.data.done) ui.timer = setTimeout(() => { if (ui.tab === "attack") loadAttack(); }, 1500);
    }

    function drawAttack(d) {
      const u = U();
      if (!d.active) {
        content.replaceChildren(h("section", { class: "panel" }, h("h2", { text: "Attack mode" }),
          h("p", { class: "muted", style: "margin:6px 0 14px", text: "Send a burst of fake fraud through the real pipeline and watch the model react. The claims use demo policyholders and hospitals, show up in the insurer queue, and can be cleared afterwards." }),
          h("div", { class: "crit-list" }, PATTERNS.map(([t, x]) => h("div", { class: "crit" }, h("div", { class: "crit-top" }, h("span", { class: "crit-title", text: t })), h("p", { text: x })))),
          h("div", { style: "margin-top:16px" }, h("button", { class: "btn primary", type: "button", onclick: launch, text: "Launch attack (9 claims)" }))));
        return;
      }
      const pending = d.claims.filter(c => !c.status || c.status === "RECEIVED" || c.status === "SCORED").length;
      const kinds = ["Upcoding", "Duplicate billing", "Provider burst"];
      content.replaceChildren(
        h("div", { class: "stats", style: "margin-bottom:0" },
          h("div", { class: "stat accent" }, h("div", { class: "stat-num num", text: String(d.total) }), h("div", { class: "stat-label", text: "Fake claims sent" })),
          h("div", { class: "stat ok" }, h("div", { class: "stat-num num", text: String(d.caught) }), h("div", { class: "stat-label", text: "Sent to a human" })),
          h("div", { class: "stat bad" }, h("div", { class: "stat-num num", text: String(d.missed) }), h("div", { class: "stat-label", text: "Slipped through (auto-approved)" })),
          h("div", { class: "stat info" }, h("div", { class: "stat-num num", text: String(pending) }), h("div", { class: "stat-label", text: "Still being scored" }))),
        h("section", { class: "panel" },
          h("div", { class: "panel-head" }, h("h2", { text: d.done ? "Result" : "Scoring..." }),
            h("button", { class: "btn", type: "button", onclick: clear, text: "Clear demo claims" })),
          d.done ? h("p", { text: `The model sent ${d.caught} of ${d.total} fake claims to a human.${d.missed ? ` ${d.missed} ${d.missed === 1 ? "was" : "were"} auto-approved. The model is not perfect, which is why humans review the rest.` : ""} Sign in as an insurer to see them in the queue.` })
                 : h("p", { class: "muted", text: "Claims are going through the scoring workflow. This page updates by itself." }),
          h("div", { class: "table-wrap", style: "margin-top:12px" }, h("table", { class: "claims" },
            h("thead", null, h("tr", null, h("th", { text: "Pattern" }), h("th", { text: "Claim" }), h("th", { class: "right", text: "Amount" }), h("th", { text: "Risk" }), h("th", { text: "Outcome" }))),
            h("tbody", null, [...d.claims].sort((a, b) => kinds.indexOf(a.pattern) - kinds.indexOf(b.pattern)).map(c => h("tr", { style: "cursor:default" },
              h("td", { text: c.pattern }),
              h("td", null, h("div", { text: `${u.PROC[c.procedure_code] || c.procedure_code || "..."}` }), h("div", { class: "muted", style: "font-size:.8125rem", text: c.provider_id || "" })),
              h("td", { class: "right num nowrap", text: c.amount ? inr(c.amount) : "" }),
              h("td", null, c.risk_score != null ? u.riskMeter(c.risk_score, c.review_queue) : h("span", { class: "muted", text: "..." })),
              h("td", null, u.statusChip(c.status)))))))));
    }

    function launch() {
      const m = modal({ title: "Launch the attack?", body: h("div", null,
        h("p", { text: "This sends 9 fake claims through the real pipeline: upcoding, duplicate billing and a provider burst. They appear in the insurer queue until you clear them." }),
        h("p", { class: "form-error", role: "alert" })),
        actions: [{ label: "Cancel" }, { label: "Launch", kind: "primary", onClick: go }] });
      async function go() {
        m.setActions([{ label: "Sending claims...", kind: "primary", disabled: true }]);
        const r = await api("/api/admin/attack", { method: "POST" });
        if (!r.ok) { m.box.querySelector(".form-error").textContent = r.data.error || "Couldn't start the attack."; m.setActions([{ label: "Close" }]); return; }
        m.close(true);
        toast(`${r.data.started} fake claims sent.`, "ok");
        loadAttack();
      }
    }

    async function clear() {
      const ok = await CG.confirm({ title: "Clear the demo claims?", message: "The fake claims are deleted from the system, including any decisions made on them.", confirmLabel: "Clear", kind: "danger" });
      if (!ok) return;
      const r = await api("/api/admin/attack/clear", { method: "POST" });
      if (!r.ok) { toast("Couldn't clear them.", "bad"); return; }
      toast(`${r.data.cleared} demo claims cleared.`, "ok");
      loadAttack();
    }

    // ---------- rules and model ----------
    async function loadRules() {
      const [o, c] = await Promise.all([ui.overview ? { ok: true, data: ui.overview } : api("/api/admin/overview"), ui.config ? { ok: true, data: ui.config } : api("/api/config")]);
      if (!o.ok || !c.ok) return failed("the rules", loadRules);
      ui.overview = o.data; ui.config = c.data;
      if (ui.tab !== "rules") return;
      const m = o.data.model, d = o.data;
      content.replaceChildren(
        h("section", { class: "panel" },
          h("div", { class: "panel-head" }, h("h2", { text: "The model" }), h("span", { class: "chip info", text: "Simulated benchmark" })),
          h("p", { class: "muted", text: `${m.type}, reading ${m.inputs} inputs per claim. These two figures come from the offline test on ${m.benchmark}, not from live claims.` }),
          h("div", { class: "benchmark" },
            h("div", null, h("div", { class: "big-num num", text: m.fraud_caught_pct + "%" }), h("div", { class: "label", text: "of fraud caught" })),
            h("div", null, h("div", { class: "big-num num", text: m.legit_delayed_pct + "%" }), h("div", { class: "label", text: "of legitimate claims delayed" })),
            h("div", null, h("div", { class: "big-num num", text: String(d.decisions.labels_for_retraining) }), h("div", { class: "label", text: "reviewer labels collected for retraining" })))),
        U().criteriaView(c.data),
        h("p", { class: "muted", style: "font-size:.875rem", text: "The routing thresholds above are read-only here. Changing them from this page is not switched on." }));
    }
  }

  CG.apps.admin = { mount };
})();
