/* ============================================================
   Virtual Accounts API — Interactive Console
   A declarative client: every endpoint is described as data and
   rendered into a form. All requests hit the configured base URL
   (defaults to same origin, so no CORS is involved).
   ============================================================ */
(function () {
  "use strict";

  // ---- Config persistence ------------------------------------------------
  const LS_BASE = "vapi.base";
  const LS_KEY = "vapi.key";
  const cfgBase = document.getElementById("cfgBase");
  const cfgKey = document.getElementById("cfgKey");

  cfgBase.value = localStorage.getItem(LS_BASE) || window.location.origin;
  cfgKey.value = localStorage.getItem(LS_KEY) || "";
  cfgBase.addEventListener("change", () => localStorage.setItem(LS_BASE, cfgBase.value.trim()));
  cfgKey.addEventListener("change", () => localStorage.setItem(LS_KEY, cfgKey.value.trim()));

  const baseUrl = () => (cfgBase.value.trim() || window.location.origin).replace(/\/+$/, "");
  const apiKey = () => cfgKey.value.trim();

  // ---- Helpers -----------------------------------------------------------
  const uuid = () =>
    (crypto.randomUUID && crypto.randomUUID()) ||
    "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });

  const EVENT_TYPES = [
    "account.created", "account.status_changed", "transfer.posted",
    "transfer.failed", "deposit.posted", "withdrawal.posted", "withdrawal.failed",
  ];
  const RAILS = ["ach", "wire", "rtp", "internal_test"];

  // ---- Operation registry ------------------------------------------------
  // field: { name, label, in:'path'|'query'|'body', type, required, placeholder,
  //          help, options, default, money }
  const T = (name, label, opts = {}) => ({ name, label, in: "body", type: "text", ...opts });
  const META = T("metadata", "Metadata (JSON)", { in: "body", type: "json", full: true, placeholder: '{ "note": "demo" }', help: "Optional. Up to 50 keys." });

  const OPS = [
    // ===== ACCOUNTS =====
    { id: "acct.create", group: "Accounts", method: "POST", path: "/accounts", idem: true,
      title: "Create account", desc: "Creates a virtual account in the pending state with a zero balance.",
      fields: [
        T("customer_id", "Customer ID", { required: true, placeholder: "cus_demo" }),
        T("currency", "Currency", { required: true, placeholder: "USD", default: "USD", help: "ISO 4217" }),
        META,
      ] },
    { id: "acct.list", group: "Accounts", method: "GET", path: "/accounts",
      title: "List accounts", desc: "Cursor-paginated list. Filter by customer or status.",
      fields: [
        T("limit", "Limit", { in: "query", type: "number", placeholder: "20" }),
        T("status", "Status", { in: "query", type: "select", options: ["", "pending", "active", "frozen", "closed"] }),
        T("customer_id", "Customer ID", { in: "query" }),
        T("starting_after", "Starting after (cursor)", { in: "query" }),
      ] },
    { id: "acct.get", group: "Accounts", method: "GET", path: "/accounts/{account_id}",
      title: "Retrieve account", desc: "Fetch a single account by ID.",
      fields: [T("account_id", "Account ID", { in: "path", required: true, placeholder: "acct_…" })] },
    { id: "acct.update", group: "Accounts", method: "PATCH", path: "/accounts/{account_id}", idem: true,
      title: "Update account", desc: "Change status (active ⇄ frozen → closed) and/or metadata.",
      fields: [
        T("account_id", "Account ID", { in: "path", required: true, placeholder: "acct_…" }),
        T("status", "Status", { in: "body", type: "select", options: ["", "active", "frozen", "closed"] }),
        META,
      ] },
    { id: "acct.balance", group: "Accounts", method: "GET", path: "/accounts/{account_id}/balance",
      title: "Get balance", desc: "Available (posted minus pending-out) and posted balance, derived from the ledger.",
      fields: [T("account_id", "Account ID", { in: "path", required: true, placeholder: "acct_…" })] },
    { id: "acct.txns", group: "Accounts", method: "GET", path: "/accounts/{account_id}/transactions",
      title: "List transactions", desc: "Per-account ledger movements, newest first.",
      fields: [
        T("account_id", "Account ID", { in: "path", required: true, placeholder: "acct_…" }),
        T("limit", "Limit", { in: "query", type: "number", placeholder: "20" }),
      ] },

    // ===== TRANSFERS =====
    { id: "tfr.create", group: "Transfers", method: "POST", path: "/transfers", idem: true,
      title: "Create transfer", desc: "Atomically move funds between two virtual accounts. Row-locked.",
      fields: [
        T("source_account_id", "Source account", { required: true, placeholder: "acct_…" }),
        T("destination_account_id", "Destination account", { required: true, placeholder: "acct_…" }),
        T("amount", "Amount", { type: "number", required: true, money: true, placeholder: "5000" }),
        T("description", "Description", { full: true }),
        META,
      ] },
    { id: "tfr.list", group: "Transfers", method: "GET", path: "/transfers",
      title: "List transfers", desc: "Filter by account (source or destination) or status.",
      fields: [
        T("limit", "Limit", { in: "query", type: "number", placeholder: "20" }),
        T("account_id", "Account ID", { in: "query", placeholder: "acct_…" }),
        T("status", "Status", { in: "query", type: "select", options: ["", "posted", "failed", "reversed"] }),
      ] },
    { id: "tfr.get", group: "Transfers", method: "GET", path: "/transfers/{transfer_id}",
      title: "Retrieve transfer", desc: "Fetch a single transfer by ID.",
      fields: [T("transfer_id", "Transfer ID", { in: "path", required: true, placeholder: "tfr_…" })] },
    { id: "tfr.reverse", group: "Transfers", method: "POST", path: "/transfers/{transfer_id}/reversal", idem: true,
      title: "Reverse transfer", desc: "Create a full-value offsetting reversal of a posted transfer.",
      fields: [
        T("transfer_id", "Transfer ID", { in: "path", required: true, placeholder: "tfr_…" }),
        T("description", "Description", { full: true }),
        META,
      ] },

    // ===== DEPOSITS =====
    { id: "dep.create", group: "Deposits", method: "POST", path: "/deposits", idem: true,
      title: "Create deposit", desc: "Record inbound funds over an external rail. Credits the account.",
      fields: [
        T("account_id", "Account ID", { required: true, placeholder: "acct_…" }),
        T("amount", "Amount", { type: "number", required: true, money: true, placeholder: "15000" }),
        T("currency", "Currency", { required: true, default: "USD", placeholder: "USD" }),
        T("rail", "Rail", { type: "select", required: true, options: RAILS }),
        T("source_reference", "Source reference", { full: true, placeholder: "IMAD-2025-06-01-12345" }),
      ] },
    { id: "dep.get", group: "Deposits", method: "GET", path: "/deposits/{deposit_id}",
      title: "Retrieve deposit", desc: "Fetch a single deposit by ID.",
      fields: [T("deposit_id", "Deposit ID", { in: "path", required: true, placeholder: "dep_…" })] },

    // ===== WITHDRAWALS =====
    { id: "wdr.create", group: "Withdrawals", method: "POST", path: "/withdrawals", idem: true,
      title: "Create withdrawal", desc: "Initiate an outbound payment. Debits the account immediately (funds reserved).",
      fields: [
        T("account_id", "Account ID", { required: true, placeholder: "acct_…" }),
        T("amount", "Amount", { type: "number", required: true, money: true, placeholder: "5000" }),
        T("currency", "Currency", { required: true, default: "USD", placeholder: "USD" }),
        T("rail", "Rail", { type: "select", required: true, options: RAILS }),
        T("destination_reference", "Destination reference", { full: true, required: true, placeholder: "Opaque destination token" }),
      ] },
    { id: "wdr.get", group: "Withdrawals", method: "GET", path: "/withdrawals/{withdrawal_id}",
      title: "Retrieve withdrawal", desc: "Fetch a single withdrawal by ID.",
      fields: [T("withdrawal_id", "Withdrawal ID", { in: "path", required: true, placeholder: "wdr_…" })] },

    // ===== EVENTS =====
    { id: "evt.list", group: "Events", method: "GET", path: "/events",
      title: "List events", desc: "Replayable event log (30-day retention), newest first.",
      fields: [
        T("limit", "Limit", { in: "query", type: "number", placeholder: "20" }),
        T("event_type", "Event type", { in: "query", type: "select", options: ["", ...EVENT_TYPES] }),
        T("created_after", "Created after (ISO)", { in: "query", placeholder: "2025-06-01T00:00:00Z" }),
      ] },
    { id: "evt.get", group: "Events", method: "GET", path: "/events/{event_id}",
      title: "Retrieve event", desc: "Fetch a single event by ID.",
      fields: [T("event_id", "Event ID", { in: "path", required: true, placeholder: "evt_…" })] },

    // ===== WEBHOOKS =====
    { id: "whk.create", group: "Webhooks", method: "POST", path: "/webhook_endpoints", idem: true,
      title: "Create webhook endpoint", desc: "Register a push endpoint. The signing secret is returned once.",
      fields: [
        T("url", "Endpoint URL", { required: true, full: true, placeholder: "https://yourapp.example.com/webhook" }),
        T("event_types", "Event types", { type: "event_types", full: true, required: true }),
      ] },
    { id: "whk.list", group: "Webhooks", method: "GET", path: "/webhook_endpoints",
      title: "List webhook endpoints", desc: "List registered endpoints (secrets are never returned).",
      fields: [T("limit", "Limit", { in: "query", type: "number", placeholder: "20" })] },
    { id: "whk.get", group: "Webhooks", method: "GET", path: "/webhook_endpoints/{endpoint_id}",
      title: "Retrieve webhook endpoint", desc: "Fetch a single endpoint by ID.",
      fields: [T("endpoint_id", "Endpoint ID", { in: "path", required: true, placeholder: "whk_…" })] },
    { id: "whk.delete", group: "Webhooks", method: "DELETE", path: "/webhook_endpoints/{endpoint_id}",
      title: "Delete webhook endpoint", desc: "Delete an endpoint and cancel its pending deliveries.",
      fields: [T("endpoint_id", "Endpoint ID", { in: "path", required: true, placeholder: "whk_…" })] },
  ];

  const GROUP_ICONS = { Accounts: "🏦", Transfers: "🔁", Deposits: "💵", Withdrawals: "🏧", Events: "📜", Webhooks: "📡" };

  // ---- Sidebar -----------------------------------------------------------
  const sidebar = document.getElementById("sidebar");
  const byGroup = {};
  OPS.forEach((op) => { (byGroup[op.group] ||= []).push(op); });

  Object.entries(byGroup).forEach(([group, ops]) => {
    const g = document.createElement("div");
    g.className = "side-group";
    g.innerHTML = `<h4>${GROUP_ICONS[group] || ""} ${group}</h4>`;
    ops.forEach((op) => {
      const a = document.createElement("div");
      a.className = "side-link";
      a.dataset.op = op.id;
      a.innerHTML = `<span>${op.title}</span><span class="mini-verb ${op.method.toLowerCase()}">${op.method}</span>`;
      a.addEventListener("click", () => selectOp(op.id));
      g.appendChild(a);
    });
    sidebar.appendChild(g);
  });

  // ---- Render an operation ----------------------------------------------
  const opVerb = document.getElementById("opVerb");
  const opTitle = document.getElementById("opTitle");
  const opRoute = document.getElementById("opRoute");
  const opDesc = document.getElementById("opDesc");
  const formFields = document.getElementById("formFields");
  const form = document.getElementById("opForm");
  let current = null;

  function selectOp(id) {
    const op = OPS.find((o) => o.id === id);
    if (!op) return;
    current = op;
    history.replaceState(null, "", `#${id}`);

    document.querySelectorAll(".side-link").forEach((l) =>
      l.classList.toggle("active", l.dataset.op === id)
    );

    opVerb.className = "verb " + op.method.toLowerCase();
    opVerb.textContent = op.method;
    opTitle.textContent = op.title;
    opRoute.textContent = op.path;
    opDesc.textContent = op.desc;

    formFields.innerHTML = "";
    op.fields.forEach((f) => formFields.appendChild(renderField(f)));
    if (op.idem) formFields.appendChild(renderIdemField());

    // reset response
    document.getElementById("respHead").style.display = "none";
    document.getElementById("respContainer").innerHTML =
      '<div class="resp-empty">Fill the form and send a request. The response will appear here.</div>';
  }

  function renderField(f) {
    const wrap = document.createElement("div");
    wrap.className = "form-field" + (f.full ? " full" : "");
    const reqStar = f.required ? ' <span class="req">*</span>' : "";
    const tag = `[${f.in}]`;
    let control;

    if (f.type === "select") {
      const opts = f.options.map((o) => `<option value="${o}">${o || "—"}</option>`).join("");
      control = `<select name="${f.name}" data-in="${f.in}">${opts}</select>`;
    } else if (f.type === "json") {
      control = `<textarea name="${f.name}" data-in="${f.in}" data-json="1" placeholder="${f.placeholder || ""}"></textarea>`;
    } else if (f.type === "event_types") {
      const boxes = EVENT_TYPES.map(
        (t) => `<label><input type="checkbox" name="event_types" value="${t}" />${t}</label>`
      ).join("");
      control = `<div class="checkbox-grid" data-in="body" data-array="event_types">${boxes}</div>`;
    } else {
      const t = f.type === "number" ? "number" : "text";
      const def = f.default ? ` value="${f.default}"` : "";
      control = `<input type="${t}" name="${f.name}" data-in="${f.in}" placeholder="${f.placeholder || ""}"${def} ${f.money ? 'data-money="1"' : ""} />`;
    }

    wrap.innerHTML =
      `<label>${f.label}${reqStar} <span class="help" style="color:var(--text-3);font-weight:400">${tag}</span></label>` +
      control +
      (f.money ? `<span class="amount-hint" data-money-hint></span>` : "") +
      (f.help ? `<span class="help">${f.help}</span>` : "");

    if (f.money) {
      const input = wrap.querySelector("input");
      const hint = wrap.querySelector("[data-money-hint]");
      const upd = () => {
        const v = parseInt(input.value, 10);
        hint.textContent = isNaN(v) ? "" : "= " + (v / 100).toLocaleString("en-US", { style: "currency", currency: "USD" }) + "  (minor units)";
      };
      input.addEventListener("input", upd);
    }
    return wrap;
  }

  function renderIdemField() {
    const wrap = document.createElement("div");
    wrap.className = "form-field full";
    wrap.innerHTML =
      `<label>Idempotency-Key <span class="req">*</span> <span class="help" style="color:var(--text-3);font-weight:400">[header]</span></label>
       <div class="idem-row">
         <input type="text" name="__idem" value="${uuid()}" />
         <button type="button" class="regen-btn" title="Generate new key">↻</button>
       </div>
       <span class="help">Auto-generated. Reuse the same key to test idempotent replay.</span>`;
    wrap.querySelector(".regen-btn").addEventListener("click", () => {
      wrap.querySelector('input[name="__idem"]').value = uuid();
    });
    return wrap;
  }

  // ---- Build & send request ---------------------------------------------
  const reqPreview = document.getElementById("reqPreview");
  const sendBtn = document.getElementById("sendBtn");

  function collect() {
    let path = current.path;
    const query = new URLSearchParams();
    const body = {};
    const headers = { Authorization: `Bearer ${apiKey()}` };
    let bodyHasContent = false;

    // path + query + scalar body
    formFields.querySelectorAll("input, select, textarea").forEach((el) => {
      const name = el.name;
      if (!name || name === "__idem") return;
      if (el.type === "checkbox") return; // handled below
      const where = el.dataset.in;
      let val = el.value.trim();
      if (val === "") return;

      if (where === "path") {
        path = path.replace(`{${name}}`, encodeURIComponent(val));
      } else if (where === "query") {
        query.append(name, val);
      } else {
        // body
        if (el.dataset.json) {
          try { body[name] = JSON.parse(val); } catch { throw new Error(`Field "${name}" must be valid JSON`); }
        } else if (el.type === "number") {
          body[name] = parseInt(val, 10);
        } else {
          body[name] = val;
        }
        bodyHasContent = true;
      }
    });

    // array checkboxes (event_types)
    formFields.querySelectorAll('[data-array]').forEach((group) => {
      const key = group.dataset.array;
      const vals = Array.from(group.querySelectorAll("input:checked")).map((c) => c.value);
      if (vals.length) { body[key] = vals; bodyHasContent = true; }
    });

    // idempotency
    const idem = formFields.querySelector('input[name="__idem"]');
    if (current.idem && idem) headers["Idempotency-Key"] = idem.value.trim();

    const qs = query.toString();
    const url = baseUrl() + path + (qs ? "?" + qs : "");
    const hasBody = ["POST", "PATCH", "PUT"].includes(current.method) && bodyHasContent;
    if (hasBody) headers["Content-Type"] = "application/json";

    return { url, method: current.method, headers, body: hasBody ? body : undefined };
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!apiKey()) { renderError("Set your API key in the bar above first."); return; }

    let req;
    try { req = collect(); } catch (err) { renderError(err.message); return; }

    sendBtn.classList.add("loading");
    sendBtn.querySelector(".send-label").textContent = "Sending…";
    const t0 = performance.now();

    try {
      const res = await fetch(req.url, {
        method: req.method,
        headers: req.headers,
        body: req.body ? JSON.stringify(req.body) : undefined,
      });
      const ms = Math.round(performance.now() - t0);
      const text = await res.text();
      let json = null;
      try { json = text ? JSON.parse(text) : null; } catch { /* non-JSON */ }
      renderResponse(res, json, text, ms, req);
    } catch (err) {
      renderError(`Network error: ${err.message}. Check the Base URL and that the service is awake.`);
    } finally {
      sendBtn.classList.remove("loading");
      sendBtn.querySelector(".send-label").textContent = "Send request";
    }
  });

  // ---- Response rendering ------------------------------------------------
  const respHead = document.getElementById("respHead");
  const statusBadge = document.getElementById("statusBadge");
  const respMeta = document.getElementById("respMeta");
  const respContainer = document.getElementById("respContainer");
  const respCopy = document.getElementById("respCopy");
  let lastJsonText = "";

  function renderError(msg) {
    respHead.style.display = "none";
    respContainer.innerHTML = `<div class="error-banner">⚠️ <span>${escapeHtml(msg)}</span></div>`;
  }

  function renderResponse(res, json, rawText, ms, req) {
    respHead.style.display = "flex";
    const cls = "s" + String(res.status)[0];
    statusBadge.className = "status-badge " + cls;
    statusBadge.textContent = `${res.status} ${res.statusText}`;
    const reqId = res.headers.get("request-id") || res.headers.get("x-request-id") || "—";
    respMeta.textContent = `${req.method} ${new URL(req.url).pathname}  ·  ${ms}ms  ·  request-id ${reqId}`;

    let html = "";
    // RFC 7807 problem detail highlight
    if (!res.ok && json && (json.code || json.title)) {
      html += `<div class="error-banner">⚠️ <span><code>${escapeHtml(json.code || "error")}</code> — ${escapeHtml(json.detail || json.title || "")}</span></div>`;
    }

    if (json !== null) {
      lastJsonText = JSON.stringify(json, null, 2);
      html += `<div class="resp-body"><pre>${syntaxJson(json)}</pre></div>`;
      html += renderIdChips(json);
    } else if (rawText) {
      lastJsonText = rawText;
      html += `<div class="resp-body"><pre>${escapeHtml(rawText)}</pre></div>`;
    } else {
      lastJsonText = "";
      html += `<div class="resp-empty">No content (${res.status}).</div>`;
    }
    respContainer.innerHTML = html;

    // wire id-chips
    respContainer.querySelectorAll(".id-chip").forEach((chip) =>
      chip.addEventListener("click", () => copyText(chip.dataset.id, chip))
    );
  }

  // Offer to copy any resource IDs found in the response for chaining calls
  function renderIdChips(json) {
    const ids = new Set();
    const scan = (obj, depth) => {
      if (!obj || depth > 2) return;
      if (Array.isArray(obj)) return obj.slice(0, 5).forEach((o) => scan(o, depth + 1));
      if (typeof obj === "object") {
        for (const [k, v] of Object.entries(obj)) {
          if (k === "id" && typeof v === "string" && /_/.test(v)) ids.add(v);
          else if (k === "secret" && typeof v === "string") ids.add(v);
          else scan(v, depth + 1);
        }
      }
    };
    scan(json, 0);
    if (!ids.size) return "";
    const chips = Array.from(ids)
      .map((id) => `<span class="id-chip" data-id="${escapeHtml(id)}">⧉ ${escapeHtml(id)}</span>`)
      .join("");
    return `<div class="id-chips">${chips}</div>`;
  }

  function copyText(text, el) {
    navigator.clipboard.writeText(text).then(() => {
      if (!el) return;
      const prev = el.textContent;
      el.textContent = "Copied ✓";
      setTimeout(() => (el.textContent = prev), 1300);
    });
  }
  respCopy.addEventListener("click", () => copyText(lastJsonText, respCopy));

  // ---- Syntax highlighting for JSON --------------------------------------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function syntaxJson(obj) {
    const json = JSON.stringify(obj, null, 2);
    return escapeHtml(json).replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
      (match) => {
        let cls = "json-num";
        if (/^"/.test(match)) cls = /:$/.test(match) ? "json-key" : "json-str";
        else if (/true|false/.test(match)) cls = "json-bool";
        else if (/null/.test(match)) cls = "json-null";
        return `<span class="${cls}">${match}</span>`;
      }
    );
  }

  // ---- Live request preview ----------------------------------------------
  form.addEventListener("input", updatePreview);
  function updatePreview() {
    if (!current) return;
    try {
      const r = collect();
      reqPreview.textContent = `${r.method} ${new URL(r.url).pathname}${new URL(r.url).search}`;
    } catch { reqPreview.textContent = ""; }
  }

  // ---- Connection test ---------------------------------------------------
  const connDot = document.getElementById("connDot");
  document.getElementById("testConn").addEventListener("click", async () => {
    connDot.className = "conn-dot";
    try {
      const res = await fetch(baseUrl() + "/healthz");
      const j = await res.json();
      connDot.className = "conn-dot " + (res.ok ? "ok" : "bad");
      reqPreview.textContent = res.ok ? `Healthy · v${j.version || "?"}` : "Unhealthy";
    } catch {
      connDot.className = "conn-dot bad";
      reqPreview.textContent = "Unreachable (service may be asleep — retry in ~30s)";
    }
  });

  // ---- Boot --------------------------------------------------------------
  const initial = (location.hash || "").slice(1);
  selectOp(OPS.find((o) => o.id === initial) ? initial : OPS[0].id);
  updatePreview();
})();
