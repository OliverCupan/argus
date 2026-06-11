/* ================================================================
   app.js — Boot, WebSocket connection, event routing
   ================================================================ */

(function () {

// ── WebSocket ──────────────────────────────────────────────────
let _ws = null;
let _reconnectDelay = 1000;
const MAX_RECONNECT = 16000;

function _connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  _ws = new WebSocket(`${proto}://${location.host}/ws`);

  _ws.onopen = () => {
    console.log("[Argus] WebSocket connected");
    _reconnectDelay = 1000;
  };

  _ws.onmessage = e => {
    let event;
    try { event = JSON.parse(e.data); } catch { return; }
    _dispatch(event);
  };

  _ws.onclose = () => {
    console.warn("[Argus] WebSocket closed — reconnecting in", _reconnectDelay, "ms");
    setTimeout(_connectWS, _reconnectDelay);
    _reconnectDelay = Math.min(_reconnectDelay * 2, MAX_RECONNECT);
  };

  _ws.onerror = err => console.error("[Argus] WebSocket error", err);
}

// ── Event routing ──────────────────────────────────────────────
function _dispatch(event) {
  const { agent_name, event_type, data } = event;

  // Route to agent cards
  window.ArgusAgents?.handleEvent(event);

  // Route to shared timeline (skip noisy token_update, iteration, and connection events)
  if (event_type !== "token_update" && event_type !== "agent_iteration" && event_type !== "connections_update") {
    window.ArgusTimeline?.add(event);
  }

  // Dashboard — token stats
  if (event_type === "token_update" && data?.summary) {
    window.ArgusDashboard?.update(data.summary);
  }

  // Connection count — update badge
  if (event_type === "connections_update" && typeof data?.active === "number") {
    const badge = document.getElementById("conn-count");
    if (badge) {
      badge.textContent = data.active;
    }
  }

  // Confirm modal
  if (event_type === "confirm_required") {
    window.ArgusConfirm?.show(data.request_id, data.command);
  }

  // Task complete — unblock input immediately and mark dashboard task end
  if (event_type === "task_complete") {
    window.ArgusInput?.unblock();
    window.ArgusDashboard?.taskEnd();
  }

  // Budget exceeded — red toast
  if (event_type === "budget_exceeded") {
    const d = data || {};
    const reasons = {
      hard_cap:       "Hard cap reached",
      soft_cap:       "Soft cap reached",
      per_agent_cap:  "Per-agent limit reached",
      max_iterations: "Max iterations reached",
    };
    _showToast(`🛑 ${reasons[d.reason] || "Budget limit reached"} — ${agent_name}`, "#3a0d0d", "#fca5a5", "#991b1b");
  }

  // Compaction notification toast
  if (event_type === "compaction") {
    const d = data || {};
    let msg = "⚡ Context compacted";
    if (d.kind === "manual" || d.kind === "manual_requested") msg = "⚡ Manual compact triggered";
    else if (d.kind === "tool_output") msg = `⚡ Tool output compacted (tier ${d.tier})`;
    else if (d.kind === "history_trim") msg = `⚡ History trimmed — ${d.messages_dropped || 0} messages dropped`;
    _showToast(msg);
  }
}

function _showToast(message, bg, fg, border) {
  bg     = bg     || "#2a1a4a";
  fg     = fg     || "#c084fc";
  border = border || "#7c3aed";

  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.style.cssText = [
      "position:fixed", "top:56px", "right:16px", "z-index:9999",
      "display:flex", "flex-direction:column", "gap:8px", "pointer-events:none"
    ].join(";");
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.style.cssText = [
    `background:${bg}`, `color:${fg}`,
    `border:1px solid ${border}`, "border-radius:6px",
    "padding:8px 14px", "font-size:12px",
    "opacity:1", "transition:opacity 0.4s ease",
    "white-space:nowrap"
  ].join(";");
  toast.textContent = message;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 400);
  }, 2000);
}

// Convert scientific notation floats to full decimal strings for input display
// e.g. 1e-7 → "0.0000001", 2.5e-8 → "0.000000025", 500000 → "500000"
function _fmtBudget(v) {
  if (typeof v !== "number" || !isFinite(v)) return String(v);
  // String() uses scientific notation only for |v| >= 1e21 or |v| < 1e-6 (i.e. 1e-7 and below)
  const s = String(v);
  if (!s.includes("e")) return s;  // already a clean decimal string
  // Parse the scientific notation and expand to decimal
  const [mantissa, expStr] = s.split("e");
  const exp = parseInt(expStr, 10);
  const [intPart, fracPart = ""] = mantissa.replace("-", "").split(".");
  const negative = v < 0;
  const digits = intPart + fracPart;
  let result;
  if (exp < 0) {
    // e.g. 1e-7: shift decimal left
    const absExp = Math.abs(exp);
    result = "0." + "0".repeat(absExp - intPart.length) + digits;
  } else {
    // e.g. 1e+21: shift decimal right
    result = digits.padEnd(digits.length + exp - fracPart.length, "0");
    if (exp < fracPart.length) {
      result = intPart + digits.slice(intPart.length, intPart.length + exp) + "." + digits.slice(intPart.length + exp);
    }
  }
  // Strip trailing zeros after decimal point
  if (result.includes(".")) result = result.replace(/\.?0+$/, "");
  return negative ? "-" + result : result;
}

// Normalize locale-formatted number strings to a JS-parseable float.
// Rules:
//   - If both '.' and ',' appear: the LAST one is the decimal separator; strip the other.
//   - If only ',' appears: treat as decimal separator → replace with '.'.
//   - If only '.' appears: treat as decimal separator (standard).
//   - Strip spaces (thousands-space separator).
function _parseLocalFloat(s) {
  s = s.trim().replace(/\s/g, "");
  const hasDot   = s.includes(".");
  const hasComma = s.includes(",");
  if (hasDot && hasComma) {
    const lastDot   = s.lastIndexOf(".");
    const lastComma = s.lastIndexOf(",");
    if (lastComma > lastDot) {
      // comma is decimal separator: remove dots, replace comma with dot
      s = s.replace(/\./g, "").replace(",", ".");
    } else {
      // dot is decimal separator: remove commas
      s = s.replace(/,/g, "");
    }
  } else if (hasComma && !hasDot) {
    // Only commas: could be thousands (1,000,000) or decimal (0,5)
    // Heuristic: if multiple commas, all are thousands separators; if one comma, it's decimal
    const commaCount = (s.match(/,/g) || []).length;
    if (commaCount > 1) {
      s = s.replace(/,/g, "");  // thousands separators
    } else {
      s = s.replace(",", ".");  // decimal separator
    }
  }
  // hasDot && !hasComma: leave as-is (standard decimal)
  return parseFloat(s);
}

// ── Settings panel ─────────────────────────────────────────────
async function _loadSettings() {
  const body = document.getElementById("settings-body");
  if (!body) return;
  try {
    const res  = await fetch("/api/config");
    const cfg  = await res.json();
    const { models, budget } = cfg;

    const modelRows = Object.entries(models).map(([k, v]) =>
      `<tr><td>${k}</td><td><input class="settings-input" data-agent="${k}" value="${v}" /></td></tr>`
    ).join("");

    const budgetRows = Object.entries(budget)
      .filter(([k]) => ["total_hard_cap","total_soft_cap","dollar_hard_cap","dollar_soft_cap"].includes(k))
      .map(([k, v]) =>
        `<tr><td>${k}</td><td><input class="settings-input" data-budget="${k}" value="${_fmtBudget(v)}" /></td></tr>`
      ).join("");

    body.innerHTML = `
      <h3 style="margin-bottom:10px">Models</h3>
      <table class="settings-table"><tbody>${modelRows}</tbody></table>
      <h3 style="margin:14px 0 10px">Budget</h3>
      <table class="settings-table"><tbody>${budgetRows}</tbody></table>
      <div style="margin-top:14px">
        <button class="btn-approve" id="settings-save">Save</button>
      </div>
    `;

    document.getElementById("settings-save")?.addEventListener("click", async () => {
      let saveOk = true;
      // Model updates
      for (const el of body.querySelectorAll("[data-agent]")) {
        const agent = el.dataset.agent;
        const model = el.value.trim();
        if (model && model !== models[agent]) {
          try {
            const res = await fetch("/api/config/model", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ agent, model }),
            });
            if (!res.ok) { _showToast(`Failed to save model: ${agent}`, "#3a0d0d", "#fca5a5", "#991b1b"); saveOk = false; }
          } catch { _showToast("Save error", "#3a0d0d", "#fca5a5", "#991b1b"); saveOk = false; }
        }
      }
      // Budget updates
      for (const el of body.querySelectorAll("[data-budget]")) {
        const field = el.dataset.budget;
        const value = _parseLocalFloat(el.value);
        if (!isNaN(value)) {
          try {
            const res = await fetch("/api/config/budget", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ field, value }),
            });
            if (!res.ok) { _showToast(`Failed to save: ${field}`, "#3a0d0d", "#fca5a5", "#991b1b"); saveOk = false; }
          } catch { _showToast("Save error", "#3a0d0d", "#fca5a5", "#991b1b"); saveOk = false; }
        }
      }
      if (saveOk) _showToast("Settings saved");
      document.getElementById("settings-overlay")?.classList.add("hidden");
    });

  } catch (err) {
    body.innerHTML = `<p style="color:var(--red)">Failed to load config: ${err.message}</p>`;
  }
}

// ── Initialize connection badge ────────────────────────────────
async function _initConnectionBadge() {
  try {
    const res = await fetch("/api/connections");
    if (res.ok) {
      const data = await res.json();
      if (typeof data.active === "number") {
        const badge = document.getElementById("conn-count");
        if (badge) {
          badge.textContent = data.active;
        }
      }
    }
  } catch (err) {
    // Fail silently — badge will update via WebSocket event
    console.debug("[Argus] Failed to fetch initial connection count:", err);
  }
}

// ── Boot ───────────────────────────────────────────────────────
function _boot() {
  window.ArgusAgents?.init();
  window.ArgusTimeline?.init();
  window.ArgusInput?.init();

  _initConnectionBadge();
  _connectWS();

  // Compact button
  document.getElementById("compact-btn")
    ?.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/compact", { method: "POST" });
        if (res.ok) {
          _showToast("⚡ Compact requested");
        }
      } catch (err) {
        console.error("[Argus] Compact request failed:", err);
      }
    });

  // Settings panel open/close
  document.getElementById("settings-btn")
    ?.addEventListener("click", () => {
      document.getElementById("settings-overlay")?.classList.remove("hidden");
      _loadSettings();
    });
  document.getElementById("settings-close")
    ?.addEventListener("click", () => {
      document.getElementById("settings-overlay")?.classList.add("hidden");
    });
  document.getElementById("settings-overlay")
    ?.addEventListener("click", e => {
      if (e.target === e.currentTarget)
        e.currentTarget.classList.add("hidden");
    });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _boot);
} else {
  _boot();
}

})();
