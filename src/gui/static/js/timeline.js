/* ================================================================
   timeline.js — Shared event feed with filtering and expandable rows
   ================================================================ */

const MAX_TIMELINE_ENTRIES = 300;
let _activeFilter = "all";
let _entries = [];

// Agent → color map (populated from AGENTS array)
function _agentColor(name) {
  const a = (window.ArgusAgents?.AGENTS || []).find(x => x.name === name);
  return a ? a.color : "#6e6ea0";
}

function _agentColorDim(name) {
  const a = (window.ArgusAgents?.AGENTS || []).find(x => x.name === name);
  return a ? a.colorDim : "rgba(110,110,160,0.12)";
}

// Format a timestamp float into HH:MM:SS
function _fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toTimeString().slice(0, 8);
}

// Human-readable label for event types
function _eventLabel(type, data) {
  switch (type) {
    case "agent_started":    return `▶ started — ${data.task_preview || ""}`;
    case "agent_iteration":  return `↺ iteration ${data.iteration}/${data.max_iterations}`;
    case "tool_call":        return `<span class="tag-tool">⚙ ${data.tool_name}</span> ${_esc(data.input_preview || "").slice(0, 80)}`;
    case "tool_result":      return data.is_error
      ? `<span class="tag-error">✗ ${data.tool_name}</span> ${_esc(data.result_preview || "").slice(0, 80)}`
      : `✓ ${data.tool_name} (${data.duration_ms}ms)`;
    case "agent_text":       return `"${_esc((data.content || "").slice(0, 100))}"`;
    case "agent_finished":   return `<span class="tag-done">✔ finished</span> — ${((data.tokens_in||0)+(data.tokens_out||0)).toLocaleString()} tokens`;
    case "status_update":    return `• ${_esc(data.message || "")}`;
    case "token_update":     return `≡ token update`;
    case "confirm_required": return `⚠ <span class="tag-error">review required</span>: ${_esc(data.command || "").slice(0, 80)}`;
    case "task_complete":    return `<span class="tag-done">★ task complete</span>`;
    case "compaction":       return `⚡ compacted`;
    default:                 return _esc(type);
  }
}

function _esc(s) {
  return String(s)
    .replace(/&/g,"&amp;")
    .replace(/</g,"&lt;")
    .replace(/>/g,"&gt;");
}

function _filterCategory(event_type) {
  if (event_type === "tool_call" || event_type === "tool_result") return "tool_call";
  if (event_type === "agent_text") return "agent_text";
  if (event_type === "status_update") return "status_update";
  return "all";
}

function _isVisible(entry) {
  if (_activeFilter === "all") return true;
  if (_activeFilter === "error") {
    return (entry.event_type === "tool_result" && entry.data.is_error)
        || entry.event_type === "confirm_required";
  }
  return _filterCategory(entry.event_type) === _activeFilter;
}

function _buildBudgetRow(entry) {
  const row = document.createElement("div");
  row.className = "tl-budget-badge";
  row.dataset.eventType = "budget_exceeded";
  row.style.cssText = [
    "display:flex", "align-items:center", "justify-content:center",
    "padding:6px 16px", "margin:4px 0",
    "background:#3a0d0d", "border-top:1px solid #991b1b",
    "border-bottom:1px solid #991b1b", "border-radius:4px",
    "font-size:11px", "color:#fca5a5", "letter-spacing:0.3px",
    "font-style:italic"
  ].join(";");

  const d = entry.data || {};
  const reasons = {
    hard_cap:      "hard cap reached — agent stopped",
    soft_cap:      "soft cap reached — agent winding down",
    per_agent_cap: "per-agent limit reached — agent stopped",
    max_iterations: "max iterations reached — agent stopped",
  };
  const label = `🛑 Budget: ${reasons[d.reason] || d.reason || "limit reached"} (${entry.agent_name})`;
  row.innerHTML = `<span style="opacity:0.9">${_esc(label)}</span>`;
  return row;
}

function _buildCompactionRow(entry) {
  const d = entry.data || {};
  const isToolOutput = d.kind === "tool_output" && (d.before || d.after);
  const isManual     = d.kind === "manual" || d.kind === "manual_requested";
  const isHistoryTrim = d.kind === "history_trim";
  const hasDetail = isToolOutput || isManual || isHistoryTrim;

  let label = "⚡ Context compacted";
  if (d.kind === "tool_output") {
    label += ` · tool output tier-${d.tier}`;
    if (d.tokens_saved_est > 0) label += ` · ~${d.tokens_saved_est.toLocaleString()} tokens saved`;
  } else if (isHistoryTrim) {
    label += ` · history trim`;
    if (d.messages_dropped > 0) label += ` · ${d.messages_dropped} messages dropped`;
  } else if (isManual) {
    label += ` · manual trigger`;
  }
  if (hasDetail) label += ` — click to expand`;

  const row = document.createElement("div");
  row.className = "tl-compact-badge";
  row.dataset.eventType = "compaction";

  const headerStyle = [
    "display:flex", "align-items:center", "justify-content:center",
    "padding:6px 16px",
    "background:#1e0d3a", "border-top:1px solid #4c1d95",
    "border-bottom:1px solid #4c1d95", "border-radius:4px",
    "font-size:11px", "color:#c084fc", "letter-spacing:0.3px",
    "font-style:italic", "cursor:pointer"
  ].join(";");

  row.innerHTML = `<div class="tl-compact-header" style="${headerStyle}">
    <span style="opacity:0.9">${_esc(label)}</span>
    <span class="tl-compact-chevron" style="margin-left:8px;font-style:normal;font-size:10px">▼</span>
  </div>`;

  // Build body content based on kind
  let bodyHTML = "";
  if (isToolOutput) {
    bodyHTML =
      `<div style="color:#a78bfa;font-weight:600;margin-bottom:6px">BEFORE — raw tool output sent to Haiku</div>` +
      `<pre style="color:#e2d9f3;white-space:pre-wrap;margin:0 0 14px">${_esc(d.before || "")}</pre>` +
      `<div style="color:#34d399;font-weight:600;margin-bottom:6px">AFTER — Haiku summary stored in context</div>` +
      `<pre style="color:#a7f3d0;white-space:pre-wrap;margin:0">${_esc(d.after || "")}</pre>`;
  } else if (isManual) {
    if (d.before || d.after) {
      const n = d.messages_dropped || 0;
      bodyHTML =
        `<div style="color:#a78bfa;font-weight:600;margin-bottom:6px">BEFORE — ${n} message${n !== 1 ? "s" : ""} trimmed from history</div>` +
        `<pre style="color:#e2d9f3;white-space:pre-wrap;margin:0 0 14px">${_esc(d.before || "")}</pre>` +
        `<div style="color:#34d399;font-weight:600;margin-bottom:6px">AFTER — messages kept in context</div>` +
        `<pre style="color:#a7f3d0;white-space:pre-wrap;margin:0">${_esc(d.after || "")}</pre>`;
    } else {
      bodyHTML =
        `<div style="color:#a78bfa;font-weight:600;margin-bottom:8px">Manual compact — no history to trim yet</div>` +
        `<p style="color:#c4b5fd;margin:0">Run a task first, then click Compact to see before/after.</p>`;
    }
  } else if (isHistoryTrim) {
    const n = d.messages_dropped || 0;
    bodyHTML =
      `<div style="color:#a78bfa;font-weight:600;margin-bottom:6px">BEFORE — ${n} message${n !== 1 ? "s" : ""} dropped from history</div>` +
      `<pre style="color:#e2d9f3;white-space:pre-wrap;margin:0 0 14px">${_esc(d.before || "(no preview)")}</pre>` +
      `<div style="color:#34d399;font-weight:600;margin-bottom:6px">AFTER — messages kept in context</div>` +
      `<pre style="color:#a7f3d0;white-space:pre-wrap;margin:0">${_esc(d.after || "(no preview)")}</pre>`;
  }

  const body = document.createElement("div");
  body.className = "tl-compact-body";
  body.style.cssText = [
    "display:none",
    "background:#120822", "border:1px solid #4c1d95", "border-top:none",
    "border-radius:0 0 4px 4px", "padding:10px 14px",
    "font-size:11px", "font-family:var(--font-mono,monospace)",
    "overflow-x:auto", "line-height:1.5"
  ].join(";");
  body.innerHTML = bodyHTML;
  row.appendChild(body);

  row.querySelector(".tl-compact-header").addEventListener("click", () => {
    const open = body.style.display !== "none";
    body.style.display = open ? "none" : "block";
    const chev = row.querySelector(".tl-compact-chevron");
    if (chev) chev.textContent = open ? "▼" : "▲";
  });

  return row;
}

function _buildRow(entry) {
  if (entry.event_type === "budget_exceeded") {
    return _buildBudgetRow(entry);
  }
  // Compaction events get their own badge row
  if (entry.event_type === "compaction") {
    return _buildCompactionRow(entry);
  }

  const row = document.createElement("div");
  row.className = "tl-entry";
  row.dataset.eventType = entry.event_type;
  if (!_isVisible(entry)) row.classList.add("hidden");

  const color    = _agentColor(entry.agent_name);
  const colorDim = _agentColorDim(entry.agent_name);

  // Detail content (shown on expand)
  let detailText = "";
  if (entry.event_type === "agent_text" && entry.data.content)
    detailText = entry.data.content;
  else if (entry.event_type === "tool_result" && entry.data.result_preview)
    detailText = entry.data.result_preview;
  else if (entry.event_type === "task_complete" && entry.data.result_markdown)
    detailText = entry.data.result_markdown.slice(0, 1000);

  row.innerHTML = `
    <span class="tl-time">${_fmtTime(entry.timestamp)}</span>
    <span class="tl-badge" style="--agent-color:${color};--agent-color-dim:${colorDim}">
      ${entry.agent_name.replace("_auditor","").replace("_"," ")}
    </span>
    <div class="tl-msg">
      ${_eventLabel(entry.event_type, entry.data)}
      ${detailText ? `<div class="tl-detail">${_esc(detailText)}</div>` : ""}
    </div>
  `;

  if (detailText) {
    row.addEventListener("click", () => row.classList.toggle("expanded"));
  }

  return row;
}

function addTimelineEntry(event) {
  _entries.push(event);
  if (_entries.length > MAX_TIMELINE_ENTRIES) _entries.shift();

  const feed = document.getElementById("timeline-feed");
  if (!feed) return;

  // Remove empty placeholder
  const empty = feed.querySelector(".timeline-empty");
  if (empty) empty.remove();

  const row = _buildRow(event);
  feed.appendChild(row);

  // Auto-scroll to bottom
  feed.scrollTop = feed.scrollHeight;
}

function applyFilter(filter) {
  _activeFilter = filter;
  const feed = document.getElementById("timeline-feed");
  if (!feed) return;
  feed.querySelectorAll(".tl-entry").forEach(row => {
    const et = row.dataset.eventType;
    const visible = filter === "all"
      || (filter === "error" && (row.querySelector(".tag-error")))
      || _filterCategory(et) === filter;
    row.classList.toggle("hidden", !visible);
  });
}

function clearTimeline() {
  _entries = [];
  const feed = document.getElementById("timeline-feed");
  if (feed) feed.innerHTML = '<div class="timeline-empty">Waiting for activity…</div>';
}

function initTimeline() {
  document.getElementById("clear-timeline-btn")
    ?.addEventListener("click", clearTimeline);

  document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      applyFilter(btn.dataset.filter);
    });
  });
}

window.ArgusTimeline = {
  init: initTimeline,
  add: addTimelineEntry,
  clear: clearTimeline,
};
