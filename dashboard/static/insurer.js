"use strict";
/* Insurer console: claims queue, case review with decision pop-ups, fraud criteria, account lookup. */
(() => {
  const { h, api, toast, modal, inr, dateFmt, state } = CG;

  const PROC = { CONS01: "Consultation", XRAY02: "X-ray", BLOOD03: "Blood tests", MRI04: "MRI scan",
    SURG05: "Surgery, standard", SURG06: "Major surgery" };
  const DX = { fever: "Fever", fracture: "Fracture", back_pain: "Back pain", appendicitis: "Appendicitis", cardiac: "Cardiac condition" };
  const QUEUES = { PRIORITY: { label: "Priority", kind: "bad" }, REVIEW: { label: "Review", kind: "warn" },
    VERIFICATION: { label: "Verification", kind: "info" } };
  const STATUS = { HISTORY: ["Settled", "muted"], RECEIVED: ["Received", "info"], SCORED: ["Scored", "info"],
    AUTO_APPROVED: ["Auto-approved", "ok"], PENDING_REVIEW: ["In review", "warn"], PRIORITY_REVIEW: ["Priority review", "bad"],
    ESCALATED: ["Escalated", "bad"], MANUAL_VERIFICATION: ["Manual verification", "info"],
    REVIEWER_APPROVED: ["Approved by reviewer", "ok"], REVIEWER_REJECTED: ["Rejected by reviewer", "bad"],
    SCORING_FAILED: ["Scoring failed", "warn"] };

  const pretty = k => String(k).replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
  const row = (k, v) => h("div", null, h("dt", { text: k }), h("dd", null, v));
  const score100 = s => { const n = Number(s || 0); return Math.round(n <= 1 ? n * 100 : n); };
  const when = iso => iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const fmtVal = v => typeof v === "number" ? (Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000))
    : v === true ? "Yes" : v === false ? "No" : String(v);
  const statusChip = s => { const [t, k] = STATUS[s] || [pretty(s || "unknown"), "muted"]; return h("span", { class: "chip " + k, text: t }); };
  const queueChip = q => QUEUES[q] ? h("span", { class: "chip " + QUEUES[q].kind, text: QUEUES[q].label }) : null;

  function riskMeter(score, queue, large) {
    const v = score100(score), kind = (QUEUES[queue] || {}).kind || "info";
    return h("div", { class: "risk" + (large ? " large" : "") },
      h("span", { class: "risk-num num", text: String(v) }),
      h("div", { class: "risk-bar", role: "img", "aria-label": `Risk ${v} out of 100` },
        h("div", { class: "risk-fill " + kind, style: `width:${Math.min(v, 100)}%` })));
  }

  // Shows every stored field of an account as label/value rows, so new fields appear without code changes.
  const HIDE = new Set(["account_id", "kind", "full_name", "name", "patient_id", "provider_id"]);
  function profileRows(profile) {
    return h("dl", { class: "rows" }, Object.entries(profile)
      .filter(([k, v]) => !HIDE.has(k) && v !== null && typeof v !== "object")
      .map(([k, v]) => row(pretty(k), /limit|sum_insured|premium/.test(k) && typeof v === "number" ? inr(v) : String(v))));
  }
  function summaryRows(kind, s) {
    if (!s) return null;
    if (kind === "CLAIMANT") {
      return h("dl", { class: "rows" }, row("Claims filed", String(s.claims_total)), row("Total claimed", inr(s.total_claimed)),
        row("By status", h("span", null, Object.entries(s.by_status).map(([k, n]) => h("span", { class: "chip muted", text: `${k} ${n}`, style: "margin:0 6px 4px 0" })))));
    }
    return h("dl", { class: "rows" }, row("Claims, all time", String(s.claims_total)), row("Claims, last 30 days", String(s.claims_last_30d)),
      row("Average claim", inr(s.avg_amount)), row("Flagged claims", String(s.flagged)));
  }

  async function openAccount(id) {
    const m = modal({ title: "Account details", wide: true, body: h("p", { class: "muted", text: "Loading..." }), actions: [{ label: "Close", kind: "primary" }] });
    const r = await api("/api/accounts/" + encodeURIComponent(id));
    if (!m.isOpen()) return;
    if (!r.ok) { m.setBody(h("p", { class: "form-error", text: "No claimant or hospital with that ID." })); return; }
    const p = r.data.profile;
    m.setTitle(p.full_name || p.name);
    m.setBody(h("div", null,
      h("p", { class: "muted", text: `${pretty(p.kind.toLowerCase())} · ${p.account_id}` }),
      h("div", { style: "margin-top:12px" }, profileRows(p)),
      h("div", { class: "section-title", text: "Activity" }), summaryRows(p.kind, r.data.summary)));
  }

  // ---------- decision pop-ups ----------
  const DECISION = {
    APPROVE: { title: "Approve this claim?", button: "Approve claim", kind: "primary", needNote: false,
      text: c => `${inr(c.amount)} will be approved for payment. The policyholder will see "Approved after review". This is recorded under your name.` },
    REJECT: { title: "Reject this claim?", button: "Reject claim", kind: "danger", needNote: true,
      text: c => `The claim for ${inr(c.amount)} will be rejected and the policyholder will see "Rejected". It is also saved as a confirmed-fraud label that can be used to retrain the model.` },
    MANUAL_VERIFICATION: { title: "Send for manual verification?", button: "Send for verification", kind: "primary", needNote: true,
      text: () => `The claim stays open in the Verification list until someone approves or rejects it. The policyholder will see "Verification in progress". Say what needs to be checked.` },
    ESCALATE: { title: "Escalate to senior review?", button: "Escalate", kind: "primary", needNote: false,
      text: () => `The claim moves to the Priority list so a senior approver can take it. The policyholder will see "Under senior review".` },
  };

  function decisionPopup(claim, decision, onDone) {
    const cfg = DECISION[decision];
    const notes = h("textarea", { id: "d-notes", maxlength: "500", placeholder: cfg.needNote ? "Required: a short reason" : "Optional note for the record" });
    const error = h("p", { class: "form-error", role: "alert" });
    const body = h("div", null, h("p", { text: cfg.text(claim) }),
      h("label", { for: "d-notes", text: cfg.needNote ? "Note (required)" : "Note (optional)" }), notes, error);
    const actions = [{ label: "Cancel" }, { label: cfg.button, kind: cfg.kind, onClick: go }];
    const m = modal({ title: cfg.title, body, actions });
    async function go() {
      error.textContent = "";
      if (cfg.needNote && notes.value.trim().length < 5) { error.textContent = "Add a short note (at least 5 characters)."; notes.focus(); return; }
      m.setActions([{ label: "Saving...", kind: cfg.kind, disabled: true }]);
      const r = await api(`/api/claims/${encodeURIComponent(claim.claim_id)}/decision`, { method: "POST", body: { decision, notes: notes.value.trim() } });
      if (!r.ok) { error.textContent = r.data.error || "Couldn't save the decision."; m.setActions(actions); return; }
      m.close(true);
      onDone(r.data);
    }
  }

  // ---------- main screen ----------
  function mount(root) {
    const me = state.profile;
    const ui = { tab: "queue", filter: "ALL", items: [], counts: { PRIORITY: 0, REVIEW: 0, VERIFICATION: 0 }, decided: 0, config: null, loaded: false };
    const stats = h("div", { class: "stats" });
    const side = h("aside", { class: "side" });
    const content = h("section", { class: "main-col" });
    const tabQueue = h("button", { class: "tab", type: "button", role: "tab", onclick: () => setTab("queue"), text: "Claims queue" });
    const tabCrit = h("button", { class: "tab", type: "button", role: "tab", onclick: () => setTab("criteria"), text: "Fraud & legitimate criteria" });

    root.replaceChildren(CG.topbar(), h("main", { class: "page" },
      h("div", { class: "page-head" }, h("div", null,
        h("h1", { text: "Claims desk" }),
        h("p", { class: "muted", text: `${me.full_name}, ${me.job_title}` }))),
      stats,
      h("div", { class: "tabs", role: "tablist" }, tabQueue, tabCrit),
      h("div", { class: "cols" }, side, content)));

    renderSide();
    setTab("queue");
    loadQueue();
    const timer = setInterval(() => {                                  // new claims show up on their own
      if (!document.body.contains(content)) { clearInterval(timer); return; }
      if (ui.tab === "queue" && !document.querySelector(".overlay")) loadQueue();
    }, 10000);

    function renderSide() {
      const lookup = h("input", { id: "lookup", placeholder: "PT00042 or P0010", "aria-label": "Account ID" });
      side.replaceChildren(
        h("section", { class: "id-card", "aria-label": "Your account" },
          h("div", { class: "pc-top" }, h("span", { text: "ClaimGuard Insurer" }), h("span", { class: "pc-plan", text: me.account_id })),
          h("div", { class: "pc-id" }, h("div", { class: "pc-name", text: me.full_name }), h("small", { text: me.job_title })),
          h("div", null, h("small", { text: "You can approve claims up to" }), h("div", { class: "limit num", text: inr(me.approval_limit) }))),
        h("section", { class: "panel" }, h("h2", { text: "Account details" }), profileRows(me)),
        h("section", { class: "panel" }, h("h2", { text: "Look up an account" }),
          h("form", { class: "lookup", onsubmit: e => { e.preventDefault(); const v = lookup.value.trim(); if (v) openAccount(v); } },
            lookup, h("button", { class: "btn", type: "submit", text: "Open" })),
          h("p", { class: "muted", style: "margin-top:8px;font-size:.875rem", text: "Policyholders start with PT, hospitals with P." })));
    }

    function renderStats() {
      const c = ui.counts;
      stats.replaceChildren(
        ...[["bad", c.PRIORITY, "Priority"], ["warn", c.REVIEW, "In review"], ["info", c.VERIFICATION, "In verification"], ["accent", ui.decided, "Decided this session"]]
          .map(([k, n, label]) => h("div", { class: "stat " + k }, h("div", { class: "stat-num num", text: String(n) }), h("div", { class: "stat-label", text: label }))));
    }

    function setTab(t) {
      ui.tab = t;
      tabQueue.setAttribute("aria-selected", String(t === "queue"));
      tabCrit.setAttribute("aria-selected", String(t === "criteria"));
      if (t === "queue") renderQueue(); else loadCriteria();
    }

    async function loadQueue() {
      const r = await api("/api/queue");
      if (!r.ok) {
        if (!ui.loaded) content.replaceChildren(h("div", { class: "panel empty" }, h("p", { text: "Couldn't load the queue." }),
          h("button", { class: "btn", type: "button", onclick: loadQueue, text: "Try again" })));
        return;
      }
      ui.loaded = true; ui.items = r.data.items; ui.counts = r.data.counts;
      renderStats();
      if (ui.tab === "queue") renderQueue();
    }

    function renderQueue() {
      if (!ui.loaded) { content.replaceChildren(h("p", { class: "muted", text: "Loading the queue..." })); return; }
      const list = ui.items.filter(i => ui.filter === "ALL" || i.review_queue === ui.filter)
        .sort((a, b) => Number(b.risk_score || 0) - Number(a.risk_score || 0));
      const filters = [["ALL", "All", ui.items.length], ...Object.entries(QUEUES).map(([k, v]) => [k, v.label, ui.counts[k]])]
        .map(([k, label, n]) => h("button", { class: "filter", type: "button", "aria-pressed": String(k === ui.filter),
          onclick: () => { ui.filter = k; renderQueue(); }, text: `${label} ${n}` }));
      content.replaceChildren(h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", { text: "Claims waiting for a decision" }), h("span", { class: "muted", text: "Highest risk first" })),
        h("div", { class: "filters", role: "group", "aria-label": "Filter by list" }, filters),
        list.length ? table(list) : h("p", { class: "empty", text: "Nothing waiting here. New flagged claims appear automatically." })));
    }

    function table(list) {
      return h("div", { class: "table-wrap" }, h("table", { class: "claims" },
        h("thead", null, h("tr", null, h("th", { text: "Risk" }), h("th", { text: "Claim" }), h("th", { class: "right", text: "Amount" }),
          h("th", { class: "hide-sm", text: "Policyholder" }), h("th", { class: "hide-sm", text: "Received" }), h("th", { class: "hide-sm", text: "List" }))),
        h("tbody", null, list.map(i => h("tr", { tabindex: "0", onclick: () => openCase(i), onkeydown: e => { if (e.key === "Enter") openCase(i); } },
          h("td", null, riskMeter(i.risk_score, i.review_queue)),
          h("td", null, h("div", { text: `${PROC[i.procedure_code] || i.procedure_code}, ${DX[i.diagnosis] || i.diagnosis}` }),
            h("div", { class: "muted", style: "font-size:.8125rem", text: `${i.provider_id} · ${dateFmt(i.claim_date)}` }),
            h("div", { class: "show-sm" }, queueChip(i.review_queue))),
          h("td", { class: "right num nowrap", text: inr(i.amount) }),
          h("td", { class: "hide-sm", text: i.patient_id }),
          h("td", { class: "hide-sm muted", text: when(i.received_at) }),
          h("td", { class: "hide-sm" }, queueChip(i.review_queue)))))));
    }

    // ---------- case review ----------
    async function openCase(item) {
      const m = modal({ title: "Claim review", wide: true, body: h("p", { class: "muted", text: "Loading the case..." }), actions: [{ label: "Close" }] });
      m.box.classList.add("xwide");
      const r = await api(`/api/claims/${encodeURIComponent(item.claim_id)}/context`);
      if (!m.isOpen()) return;
      if (!r.ok) { m.setBody(h("p", { class: "form-error", text: "Couldn't load this claim." })); return; }
      const { claim, claimant, hospital, history } = r.data;
      const limit = Number(me.approval_limit), overLimit = Number(claim.amount) > limit;
      const open = !!claim.review_queue;

      m.setBody(h("div", null,
        h("div", { class: "case-head" },
          h("div", null, h("h3", { text: `${PROC[claim.procedure_code] || claim.procedure_code} · ${DX[claim.diagnosis] || claim.diagnosis}` }),
            h("p", { class: "muted", text: `${dateFmt(claim.claim_date)} · ${hospital ? hospital.profile.name : claim.provider_id} · ${claim.claim_id.slice(0, 8)}` })),
          h("div", { class: "amount num", text: inr(claim.amount) })),
        h("div", { class: "case-grid" },
          h("div", { class: "card" }, h("h3", null, "Fraud risk", queueChip(claim.review_queue)),
            riskMeter(claim.risk_score, claim.review_queue, true),
            h("p", { class: "muted", style: "margin-top:8px", text: claim.risk_band ? `Model band: ${claim.risk_band}` : "" }),
            h("div", { style: "margin-top:8px" }, statusChip(claim.status))),
          h("div", { class: "card" }, h("h3", { text: "Quick facts" }), facts(claim, claimant, hospital, history))),
        !open ? h("div", { class: "banner info" }, h("strong", { text: "Already decided" }),
          h("span", { text: `${claim.reviewer || "A reviewer"} decided this on ${when(claim.reviewed_at)}.${claim.review_notes ? " Note: " + claim.review_notes : ""}` })) : null,
        open && overLimit ? h("div", { class: "banner warn" }, h("strong", { text: "Above your approval limit" }),
          h("span", { text: `You can approve up to ${inr(limit)}. You can still reject, send for verification, or escalate.` })) : null,
        claim.features ? h("div", null, h("div", { class: "section-title", text: "What the model saw" }), inputs(claim.features)) : null,
        h("div", { class: "case-grid" },
          claimant ? h("div", { class: "card" }, h("h3", null, "Policyholder",
            h("button", { class: "btn small", type: "button", onclick: () => openAccount(claimant.profile.account_id), text: "Full details" })),
            h("p", { style: "font-weight:600", text: claimant.profile.full_name }),
            h("dl", { class: "rows" }, row("Policy", claimant.profile.policy_number), row("Plan", `${claimant.profile.plan}, ${inr(claimant.profile.sum_insured)}`),
              row("Cover from", dateFmt(claimant.profile.cover_from)), row("Claimed so far", inr(claimant.summary.total_claimed)),
              row("KYC", claimant.profile.kyc_status))) : h("div", { class: "card muted", text: "No policyholder record for this patient ID." }),
          hospital ? h("div", { class: "card" }, h("h3", null, "Hospital",
            h("button", { class: "btn small", type: "button", onclick: () => openAccount(hospital.profile.account_id), text: "Full details" })),
            h("p", { style: "font-weight:600", text: hospital.profile.name }),
            h("dl", { class: "rows" }, row("City", hospital.profile.city), row("Network", hospital.profile.network_status || "Unknown"),
              row("Last 30 days", `${hospital.summary.claims_last_30d} ${hospital.summary.claims_last_30d === 1 ? "claim" : "claims"}`), row("Average claim", inr(hospital.summary.avg_amount)),
              row("Flagged", `${hospital.summary.flagged} of ${hospital.summary.claims_total}`))) : h("div", { class: "card muted", text: "No hospital record for this provider ID." })),
        history.length ? h("div", null, h("div", { class: "section-title", text: "Recent claims by this policyholder" }), historyTable(history)) : null,
        claim.review_notes && open ? h("p", { class: "muted", style: "margin-top:14px", text: `Earlier note from ${claim.reviewer}: ${claim.review_notes}` }) : null));

      const act = d => () => decisionPopup(claim, d, res => {
        m.close();
        ui.decided += 1;
        toast({ APPROVE: "Claim approved.", REJECT: "Claim rejected.", ESCALATE: "Escalated to senior review.", MANUAL_VERIFICATION: "Sent for manual verification." }[res.decision], "ok");
        loadQueue();
      });
      m.setActions(open ? [
        { label: "Close" },
        { label: "Reject", kind: "danger", onClick: act("REJECT") },
        { label: "Manual verification", onClick: act("MANUAL_VERIFICATION") },
        { label: "Escalate", onClick: act("ESCALATE") },
        { label: "Approve", kind: "primary", disabled: overLimit, onClick: act("APPROVE") },
      ] : [{ label: "Close", kind: "primary" }]);
    }

    function facts(claim, claimant, hospital, history) {
      const out = [], amt = Number(claim.amount);
      if (hospital && hospital.summary.avg_amount > 0) {
        const x = amt / hospital.summary.avg_amount;
        out.push([`${x.toFixed(1)}× this hospital's average claim (${inr(hospital.summary.avg_amount)})`, x >= 3]);
      }
      if (history.length) {
        const avg = history.reduce((s, c) => s + Number(c.amount), 0) / history.length;
        const x = amt / avg;
        out.push([`${x.toFixed(1)}× this policyholder's recent average (${inr(avg)})`, x >= 3]);
        const sameHosp = history.filter(c => c.provider_id === claim.provider_id).length;
        out.push([`${sameHosp} of their last ${history.length} claims were at this hospital`, false]);
      }
      if (claimant) {
        const p = claimant.profile, total = claimant.summary.total_claimed;
        out.push([`Claims total ${Math.round(total / p.sum_insured * 100)}% of the ${inr(p.sum_insured)} sum insured`, total > p.sum_insured]);
        if (claim.claim_date < p.cover_from) out.push(["Treatment date is before the policy started", true]);
      }
      if (hospital && hospital.profile.network_status && hospital.profile.network_status !== "In-network") out.push(["Hospital is outside the network", true]);
      return out.length ? h("ul", { class: "facts" }, out.map(([t, flag]) => h("li", { class: flag ? "flag" : "", text: t })))
        : h("p", { class: "muted", text: "No comparison data available." });
    }

    function inputs(f) {
      if (typeof f !== "object" || Array.isArray(f)) return null;
      return h("dl", { class: "inputs" }, Object.entries(f).sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => h("div", null, h("dt", { text: pretty(k) }), h("dd", { text: fmtVal(v) }))));
    }

    function historyTable(rows) {
      return h("div", { class: "table-wrap" }, h("table", { class: "mini" }, h("thead", null, h("tr", null, h("th", { text: "Date" }), h("th", { text: "Procedure" }),
        h("th", { class: "hide-sm", text: "Hospital" }), h("th", { class: "right", text: "Amount" }), h("th", { class: "hide-sm", text: "Status" }))),
        h("tbody", null, rows.map(c => h("tr", null, h("td", { text: dateFmt(c.claim_date) }),
          h("td", null, h("div", { text: PROC[c.procedure_code] || c.procedure_code }), h("div", { class: "show-sm" }, statusChip(c.status))),
          h("td", { class: "hide-sm", text: c.provider_id }), h("td", { class: "right num", text: inr(c.amount) }),
          h("td", { class: "hide-sm" }, statusChip(c.status)))))));
    }

    // ---------- criteria ----------
    async function loadCriteria() {
      if (!ui.config) {
        content.replaceChildren(h("p", { class: "muted", text: "Loading the criteria..." }));
        const r = await api("/api/config");
        if (!r.ok) { content.replaceChildren(h("div", { class: "panel empty" }, h("p", { text: "Couldn't load the criteria." }))); return; }
        ui.config = r.data;
      }
      if (ui.tab !== "criteria") return;
      const all = ui.config.criteria || [];
      const info = c => {
        const id = String(c.id || c.criterion_id || "");
        const kind = String(c.type || c.kind || c.category || (id.startsWith("L") ? "LEGIT" : "FRAUD")).toUpperCase();
        return { id, fraud: !kind.startsWith("L"), title: c.title || c.name || c.label || id,
          text: c.description || c.detail || c.rule || "", active: c.implemented !== false, raw: c };
      };
      const items = all.map(info);
      const SKIP = new Set(["id", "criterion_id", "type", "kind", "category", "title", "name", "label", "description", "detail", "rule", "implemented"]);
      const card = c => h("div", { class: "crit " + (c.fraud ? "" : "legit ") + (c.active ? "" : "planned") },
        h("div", { class: "crit-top" }, h("span", { class: "crit-id", text: c.id }), h("span", { class: "crit-title", text: c.title }),
          h("span", { class: "chip " + (c.active ? "ok" : "muted"), text: c.active ? "Active" : "Planned" })),
        c.text ? h("p", { text: c.text }) : null,
        h("div", { class: "attrs" }, Object.entries(c.raw).filter(([k, v]) => !SKIP.has(k) && v !== null && typeof v !== "object")
          .map(([k, v]) => h("span", { text: `${pretty(k)}: ${fmtVal(v)}` }))));
      const col = (title, sub, list) => h("section", { class: "panel" }, h("h2", { text: title }),
        h("p", { class: "muted", style: "margin:4px 0 14px", text: sub }), h("div", { class: "crit-list" }, list.map(card)));
      const fraud = items.filter(c => c.fraud), legit = items.filter(c => !c.fraud);
      const route = Object.entries(ui.config.routing || {}).filter(([, v]) => typeof v !== "object");
      content.replaceChildren(
        h("section", { class: "panel" }, h("h2", { text: "How ClaimGuard decides" }),
          h("p", { class: "muted", style: "margin-top:6px", text: `${items.filter(c => c.active).length} criteria are active and ${items.filter(c => !c.active).length} are planned. Planned rules are listed so reviewers can see what is coming, but they do not affect any claim yet.` }),
          route.length ? h("dl", { class: "rows", style: "margin-top:12px" }, route.map(([k, v]) => row(pretty(k), fmtVal(v)))) : null),
        h("div", { class: "crit-cols" },
          col("Signals of fraud", "A claim looks more suspicious when these are true.", fraud),
          col("Signals of a legitimate claim", "A claim looks safer when these are true.", legit)));
    }
  }

  CG.apps.insurer = { mount };
})();
