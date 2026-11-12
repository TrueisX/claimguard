"use strict";
/* Policyholder view: policy card, account details, claim history, claim submission. */
(() => {
  const { h, api, toast, modal, inr, dateFmt, todayISO, sleep, state } = CG;

  const PROCEDURES = {
    CONS01: "Consultation", XRAY02: "X-ray", BLOOD03: "Blood tests",
    MRI04: "MRI scan", SURG05: "Surgery, standard", SURG06: "Major surgery",
  };
  const DIAGNOSES = {
    fever: "Fever", fracture: "Fracture", back_pain: "Back pain",
    appendicitis: "Appendicitis", cardiac: "Cardiac condition",
  };

  // kind = chip colour, step = how many of the three stages are finished
  const INFO = {
    HISTORY: { kind: "muted", step: 3 },
    RECEIVED: { kind: "info", step: 1 },
    SCORED: { kind: "info", step: 2 },
    AUTO_APPROVED: { kind: "ok", step: 3 },
    PENDING_REVIEW: { kind: "warn", step: 2 },
    PRIORITY_REVIEW: { kind: "warn", step: 2 },
    ESCALATED: { kind: "warn", step: 2 },
    MANUAL_VERIFICATION: { kind: "warn", step: 2 },
    REVIEWER_APPROVED: { kind: "ok", step: 3 },
    REVIEWER_REJECTED: { kind: "bad", step: 3 },
    SCORING_FAILED: { kind: "warn", step: 2 },
  };
  const FINAL_LABEL = { AUTO_APPROVED: "Approved", REVIEWER_APPROVED: "Approved", REVIEWER_REJECTED: "Rejected", HISTORY: "Settled" };

  // [banner colour, headline, sentence]
  const OUTCOME = {
    RECEIVED: ["info", "Claim received", "We have your claim and are starting the automated check."],
    SCORED: ["info", "Checking your claim", "The automated check is finishing."],
    AUTO_APPROVED: ["ok", "Approved", "Your claim passed the automated check. Nothing more is needed from you."],
    PENDING_REVIEW: ["warn", "Sent to a reviewer", "A claims reviewer will look at this claim. The result will appear here."],
    PRIORITY_REVIEW: ["warn", "Sent to a reviewer", "A claims reviewer will look at this claim. The result will appear here."],
    ESCALATED: ["warn", "With a senior reviewer", "A senior reviewer is looking at this claim."],
    MANUAL_VERIFICATION: ["warn", "Verification needed", "We need to check some details by hand. A claims officer may contact you."],
    REVIEWER_APPROVED: ["ok", "Approved after review", "A reviewer checked this claim and approved it."],
    REVIEWER_REJECTED: ["bad", "Rejected", "A reviewer rejected this claim. Contact your insurer if you think this is a mistake."],
    SCORING_FAILED: ["warn", "Check didn't finish", "We couldn't finish the automated check on this claim."],
    HISTORY: ["info", "Settled", "This claim was settled before the new system started."],
  };

  const chipFor = (status, label) => h("span", { class: "chip " + ((INFO[status] || {}).kind || "info"), text: label });
  const field = (k, v) => h("div", null, h("dt", { text: k }), h("dd", { text: v }));
  const row = (k, v) => h("div", null, h("dt", { text: k }), h("dd", null, v));

  function policyCard(p) {
    return h("section", { class: "policy-card", "aria-label": "Your policy" },
      h("div", { class: "pc-top" }, h("span", { text: "ClaimGuard Health" }), h("span", { class: "pc-plan", text: p.plan })),
      h("div", { class: "pc-id" },
        h("div", { class: "pc-number", text: p.policy_number }),
        h("div", { class: "pc-name", text: p.full_name })),
      h("dl", { class: "pc-meta" },
        field("Members", String(p.family_members)),
        field("Cover from", dateFmt(p.cover_from)),
        field("Renews", dateFmt(p.renewal_due))));
  }

  function details(p) {
    return h("dl", { class: "rows" },
      row("Policy", h("span", { class: "chip ok", text: p.policy_status })),
      row("Sum insured", inr(p.sum_insured)),
      row("Premium", `${inr(p.annual_premium)} a year`),
      row("KYC", h("span", { class: "chip " + (p.kyc_status === "Verified" ? "ok" : "warn"), text: p.kyc_status })),
      row("Location", `${p.city}, ${p.state}`),
      row("Email", p.email),
      row("Phone", p.phone));
  }

  function coverBlock(p, s) {
    const ratio = s.total_claimed / p.sum_insured;
    const over = ratio > 1;
    return h("div", null,
      h("div", { class: "cover-figures" },
        h("span", { class: "big num", text: inr(s.total_claimed) }),
        h("span", { class: "muted", text: `claimed of ${inr(p.sum_insured)} cover` })),
      h("div", { class: "meter", role: "img", "aria-label": `${Math.round(ratio * 100)} percent of cover claimed` },
        h("div", { class: "meter-fill" + (over ? " over" : ""), style: `width:${Math.min(ratio, 1) * 100}%` })),
      over
        ? h("p", { class: "cover-note" }, h("span", { class: "chip bad", text: "Cover exceeded" }), " Your claims add up to more than your sum insured.")
        : h("p", { class: "cover-note muted", text: `${inr(p.sum_insured - s.total_claimed)} of cover remaining.` }));
  }

  function stepper(status) {
    const info = INFO[status] || { step: 1 };
    const rejected = status === "REVIEWER_REJECTED";
    const labels = ["Received", "Automated check", FINAL_LABEL[status] || "Decision"];
    return h("ol", { class: "steps", "aria-label": "Claim progress" }, labels.map((label, i) => {
      const done = i < info.step;
      const cls = done ? "done" + (i === 2 && rejected ? " bad" : "") : (i === info.step ? "current" : "");
      return h("li", { class: cls, "aria-current": i === info.step ? "step" : null },
        h("span", { class: "dot", "aria-hidden": "true", text: done ? (i === 2 && rejected ? "✕" : "✓") : "" }),
        h("span", { text: label }));
    }));
  }

  function banner(status) {
    const [kind, title, text] = OUTCOME[status] || OUTCOME.RECEIVED;
    return h("div", { class: "banner " + kind }, h("strong", { text: title }), h("span", { text }));
  }

  function mount(root) {
    const p = state.profile;
    const ui = { claims: [], summary: null, filter: "All", shown: 10, hospitals: null, lastSubmitted: null };
    const side = h("aside", { class: "side" });
    const main = h("section", { class: "main-col" });
    const first = (p.full_name || "").split(" ")[0];

    root.replaceChildren(CG.topbar(),
      h("main", { class: "page" },
        h("div", { class: "page-head" },
          h("div", null,
            h("h1", { text: `Hello, ${first}` }),
            h("p", { class: "muted", text: `Your ${p.plan} plan covers ${p.family_members} ${p.family_members === 1 ? "person" : "people"}.` })),
          h("button", { class: "btn primary big", type: "button", onclick: openSubmit, text: "Submit a claim" })),
        h("div", { class: "cols" }, side, main)));

    renderSide();
    main.append(h("p", { class: "muted", text: "Loading your claims..." }));
    load();

    function renderSide() {
      const s = ui.summary;
      side.replaceChildren(
        policyCard(p),
        h("section", { class: "panel" }, h("h2", { text: "Cover" }),
          s ? coverBlock(p, s) : h("p", { class: "muted", text: "Loading..." })),
        h("section", { class: "panel" }, h("h2", { text: "Account details" }), details(p)));
    }

    async function load() {
      const [me, mine] = await Promise.all([api("/api/me"), api("/api/my/claims")]);
      if (!me.ok || !mine.ok) {
        main.replaceChildren(h("div", { class: "panel empty" },
          h("p", { text: "We couldn't load your claims." }),
          h("button", { class: "btn", type: "button", onclick: load, text: "Try again" })));
        return;
      }
      ui.summary = me.data.summary;
      ui.claims = mine.data.claims;
      renderSide();
      renderMain();
    }

    function renderMain() {
      const counts = {};
      ui.claims.forEach(c => { counts[c.status_label] = (counts[c.status_label] || 0) + 1; });
      const list = ui.claims.filter(c => ui.filter === "All" || c.status_label === ui.filter);
      const visible = list.slice(0, ui.shown);
      const filters = ["All", ...Object.keys(counts)].map(l =>
        h("button", { class: "filter", type: "button", "aria-pressed": String(l === ui.filter),
          onclick: () => { ui.filter = l; ui.shown = 10; renderMain(); },
          text: `${l} ${l === "All" ? ui.claims.length : counts[l]}` }));
      main.replaceChildren(h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", { text: "Your claims" })),
        h("div", { class: "filters", role: "group", "aria-label": "Filter by status" }, filters),
        list.length
          ? claimsTable(visible)
          : h("p", { class: "empty", text: ui.claims.length ? "No claims with this status." : "No claims yet. Submit your first claim to see it here." }),
        list.length > visible.length
          ? h("button", { class: "btn", type: "button", onclick: () => { ui.shown += 10; renderMain(); },
              text: `Show ${Math.min(10, list.length - visible.length)} more` })
          : null));
    }

    function claimsTable(rows) {
      return h("div", { class: "table-wrap" }, h("table", { class: "claims" },
        h("thead", null, h("tr", null,
          h("th", { text: "Date" }), h("th", { text: "Hospital" }),
          h("th", { class: "hide-sm", text: "Procedure" }),
          h("th", { class: "right", text: "Amount" }), h("th", { class: "hide-sm", text: "Status" }))),
        h("tbody", null, rows.map(c => h("tr", { tabindex: "0", onclick: () => openDetail(c),
          onkeydown: e => { if (e.key === "Enter") openDetail(c); } },
          h("td", { class: "date", text: dateFmt(c.claim_date) }),
          // on a phone the status chip moves under the hospital name so nothing scrolls sideways
          h("td", null, h("div", { text: c.hospital_name }),
            h("div", { class: "show-sm" }, chipFor(c.status, c.status_label))),
          h("td", { class: "hide-sm", text: PROCEDURES[c.procedure_code] || c.procedure_code }),
          h("td", { class: "right num nowrap", text: inr(c.amount) }),
          h("td", { class: "hide-sm" }, chipFor(c.status, c.status_label)))))));
    }

    function openDetail(c) {
      const body = h("div", null,
        h("dl", { class: "rows" },
          row("Hospital", c.hospital_name),
          row("Date", dateFmt(c.claim_date)),
          row("Diagnosis", DIAGNOSES[c.diagnosis] || c.diagnosis),
          row("Procedure", `${PROCEDURES[c.procedure_code] || c.procedure_code} (${c.procedure_code})`),
          row("Amount", inr(c.amount)),
          row("Claim ID", c.claim_id)),
        stepper(c.status),
        banner(c.status));
      modal({ title: "Claim details", wide: true, body, actions: [{ label: "Close", kind: "primary" }] });
    }

    async function openSubmit() {
      if (!ui.hospitals) {
        const r = await api("/api/hospitals");
        if (!r.ok) { toast("Couldn't load the hospital list. Try again.", "bad"); return; }
        ui.hospitals = r.data.hospitals;
      }
      const today = todayISO();
      const hospital = h("input", { id: "f-hospital", list: "hospital-options", placeholder: "Start typing a hospital name", autocomplete: "off" });
      const options = h("datalist", { id: "hospital-options" },
        ui.hospitals.map(x => h("option", { value: `${x.name} (${x.provider_id})` })));
      const diagnosis = h("select", { id: "f-dx" }, Object.entries(DIAGNOSES).map(([k, v]) => h("option", { value: k, text: v })));
      const procedure = h("select", { id: "f-proc" }, Object.entries(PROCEDURES).map(([k, v]) => h("option", { value: k, text: `${v} (${k})` })));
      const amount = h("input", { id: "f-amount", type: "number", min: "1", step: "1", inputmode: "numeric", placeholder: "Amount in rupees" });
      const date = h("input", { id: "f-date", type: "date", value: today, max: today, min: p.cover_from });
      const error = h("p", { class: "form-error", role: "alert" });

      const fill = pre => {
        const hosp = ui.hospitals.find(x => x.provider_id === pre.provider_id);
        hospital.value = hosp ? `${hosp.name} (${hosp.provider_id})` : "";
        diagnosis.value = pre.diagnosis;
        procedure.value = pre.procedure_code;
        amount.value = pre.amount;
        date.value = today;
      };
      const preset = (label, data) => h("button", { class: "btn small", type: "button", onclick: () => fill(data), text: label });

      const form = h("form", { onsubmit: e => e.preventDefault() },
        h("label", { for: "f-hospital", text: "Hospital" }), hospital, options,
        h("div", { class: "form-row" },
          h("div", null, h("label", { for: "f-dx", text: "Diagnosis" }), diagnosis),
          h("div", null, h("label", { for: "f-proc", text: "Procedure" }), procedure)),
        h("div", { class: "form-row" },
          h("div", null, h("label", { for: "f-amount", text: "Amount billed" }), amount),
          h("div", null, h("label", { for: "f-date", text: "Date of treatment" }), date)),
        h("div", { class: "presets" },
          h("span", { class: "muted", text: "Demo examples:" }),
          preset("Normal visit", { provider_id: "P0010", diagnosis: "fever", procedure_code: "CONS01", amount: 820 }),
          preset("Fever billed as heart surgery", { provider_id: "P0020", diagnosis: "fever", procedure_code: "SURG06", amount: 310000 }),
          ui.lastSubmitted ? preset("Repeat my last claim", ui.lastSubmitted) : null),
        error);

      const formActions = [{ label: "Cancel" }, { label: "Submit claim", kind: "primary", onClick: submit }];
      const m = modal({ title: "Submit a claim", body: form, actions: formActions });

      async function submit() {
        error.textContent = "";
        const match = /\((P\d{4})\)\s*$/.exec(hospital.value);
        if (!match) { error.textContent = "Pick a hospital from the list."; return; }
        const amt = Number(amount.value);
        if (!(amt > 0)) { error.textContent = "Enter the amount you were billed."; return; }
        if (!date.value) { error.textContent = "Choose the date of treatment."; return; }
        const claim = { provider_id: match[1], claim_date: date.value, diagnosis: diagnosis.value,
          procedure_code: procedure.value, amount: amt };
        m.setActions([{ label: "Submitting...", kind: "primary", disabled: true }]);
        const r = await api("/api/my/claims", { method: "POST", body: claim });
        if (!r.ok) {
          error.textContent = (r.data.errors || [r.data.error || "Couldn't submit the claim."]).join(". ");
          m.setActions(formActions);
          return;
        }
        ui.lastSubmitted = claim;
        track(m, r.data.claim_id, claim);
      }
    }

    // Shows the claim moving through the automated check, then the outcome.
    async function track(m, claimId, claim) {
      let status = "RECEIVED";
      const holder = h("div", null);
      m.setTitle("Your claim");
      m.setBody(holder);
      m.setActions([{ label: "Close", kind: "primary" }]);
      const draw = () => holder.replaceChildren(
        h("p", { class: "muted", text: `${PROCEDURES[claim.procedure_code]} for ${inr(claim.amount)}` }),
        stepper(status), banner(status));
      draw();
      for (let i = 0; i < 20 && ["RECEIVED", "SCORED"].includes(status) && m.isOpen(); i++) {
        await sleep(1500);
        const r = await api("/api/my/claims");
        const c = r.ok && r.data.claims.find(x => x.claim_id === claimId);
        if (c && c.status !== status) { status = c.status; if (m.isOpen()) draw(); }
      }
      const kind = (INFO[status] || {}).kind;
      if (!["RECEIVED", "SCORED"].includes(status)) {
        toast(OUTCOME[status][1], kind === "ok" ? "ok" : kind === "bad" ? "bad" : "info");
      }
      load();
    }
  }

  CG.apps.claimant = { mount };
})();
