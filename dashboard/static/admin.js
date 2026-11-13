"use strict";
/* Platform admin console: overview, system health, accounts, rules and model. */
(() => {
  const { h, api, toast, inr, dateFmt, state } = CG;
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
    const TABS = [["overview", "Overview"], ["health", "System health"], ["accounts", "Accounts"], ["rules", "Rules & model"]];
    const tabEls = TABS.map(([k, label]) => h("button", { class: "tab", type: "button", role: "tab", "data-tab": k, onclick: () => setTab(k), text: label }));

    root.replaceChildren(CG.topbar(), h("main", { class: "page" },
      h("div", { class: "page-head" }, h("div", null, h("h1", { text: "Control room" }), h("p", { class: "muted", text: `${me.full_name}, ${me.job_title}` }))),
      h("div", { class: "tabs", role: "tablist" }, tabEls), content));

    CG.ui.onAccountChanged = () => { if (ui.tab === "accounts") loadAccounts(); };
    setTab("overview");

    function setTab(t) {
      ui.tab = t;
      tabEls.forEach(el => el.setAttribute("aria-selected", String(el.dataset.tab === t)));
      content.replaceChildren(h("p", { class: "muted", text: "Loading..." }));
      ({ overview: loadOverview, health: loadHealth, accounts: loadAccounts, rules: loadRules })[t]();
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
