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

  // Task complete — mark dashboard task end
  if (event_type === "task_complete") {
    window.ArgusDashboard?.taskEnd();
  }

  // Compaction notification toast
  if (event_type === "compaction") {
    const d = data || {};
    let msg = "⚡ Context compacted";
    if (d.kind === "manual" || d.kind === "manual_requested") msg = "⚡ Manual compact triggered";
    else if (d.kind === "tool_output") msg = `⚡ Tool output compacted (tier ${d.tier})`;
    else if (d.kind === "history_trim") msg = `⚡ History trimmed (${d.messages_dropped || 0} messages)`;
    _showToast(msg);
  }
}

function _showToast(message) {
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
    "background:#2a1a4a", "color:#c084fc",
    "border:1px solid #7c3aed", "border-radius:6px",
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
        `<tr><td>${k}</td><td><input class="settings-input" data-budget="${k}" value="${v}" /></td></tr>`
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
      // Model updates
      for (const el of body.querySelectorAll("[data-agent]")) {
        const agent = el.dataset.agent;
        const model = el.value.trim();
        if (model && model !== models[agent]) {
          await fetch("/api/config/model", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ agent, model }),
          });
        }
      }
      // Budget updates
      for (const el of body.querySelectorAll("[data-budget]")) {
        const field = el.dataset.budget;
        const value = parseFloat(el.value);
        if (!isNaN(value) && value !== budget[field]) {
          await fetch("/api/config/budget", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ field, value }),
          });
        }
      }
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
