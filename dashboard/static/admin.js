"use strict";
/* Platform admin console: overview, system health, accounts, rules and model. */
(() => {
  const { h, api, toast, modal, inr, dateFmt, state } = CG;
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
    const TABS = [["overview", "Overview"], ["health", "System health"], ["accounts", "Accounts"], ["rules", "Rules & model"], ["attack", "Attack mode"]];
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
      ({ overview: loadOverview, health: loadHealth, accounts: loadAccounts, rules: loadRules, attack: loadAttack })[t]();
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
