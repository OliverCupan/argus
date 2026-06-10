/* ================================================================
   dashboard.js — Token / cost gauges, sidebar metrics, task timing
   ================================================================ */

let _lastSnapshot = { total_tokens: 0, total_cost_usd: 0 };
let _taskStart    = { total_tokens: 0, total_cost_usd: 0 };
let _taskStartTime = null;
let _taskTimerHandle = null;

function fmtTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function fmtCost(n) { return "$" + n.toFixed(4); }

function _set(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function updateDashboard(summary) {
  if (!summary) return;

  const {
    total_tokens, total_cost_usd,
    hard_cap, dollar_hard_cap,
    cache_creation_tokens, cache_read_tokens,
  } = summary;

  // ── Budget gauges ────────────────────────────────────────────
  const tokenPct = hard_cap > 0 ? Math.min(100, total_tokens / hard_cap * 100) : 0;
  const costPct  = dollar_hard_cap > 0 ? Math.min(100, total_cost_usd / dollar_hard_cap * 100) : 0;

  const tokenFill = document.getElementById("token-fill");
  if (tokenFill) {
    tokenFill.style.width = tokenPct + "%";
    tokenFill.classList.toggle("warn",   tokenPct >= 70 && tokenPct < 90);
    tokenFill.classList.toggle("danger", tokenPct >= 90);
  }
  const costFill = document.getElementById("cost-fill");
  if (costFill) {
    costFill.style.width = costPct + "%";
    costFill.classList.toggle("warn",   costPct >= 70 && costPct < 90);
    costFill.classList.toggle("danger", costPct >= 90);
  }

  // Gauge text
  _set("token-value", `${fmtTokens(total_tokens)} / ${fmtTokens(hard_cap)}`);
  _set("cost-value",  `${fmtCost(total_cost_usd)} / $${dollar_hard_cap.toFixed(2)}`);

  // Big numbers
  _set("tok-big",      fmtTokens(total_tokens));
  _set("tok-big-sub",  `/ ${fmtTokens(hard_cap)}`);
  _set("cost-big",     "$" + total_cost_usd.toFixed(2));
  _set("cost-big-sub", `/ $${dollar_hard_cap.toFixed(2)}`);

  // ── Session stats ────────────────────────────────────────────
  _set("stat-total-tok",    fmtTokens(total_tokens));
  _set("stat-cache-write",  cache_creation_tokens != null ? fmtTokens(cache_creation_tokens) : "—");
  _set("stat-cache-read",   cache_read_tokens     != null ? fmtTokens(cache_read_tokens)     : "—");
  _set("stat-total-cost",   fmtCost(total_cost_usd));

  // ── Per-agent bars from summary ──────────────────────────────
  const perAgent = summary.per_agent || {};
  let maxTok = 1;
  Object.values(perAgent).forEach(v => { if (v.tokens > maxTok) maxTok = v.tokens; });
  Object.entries(perAgent).forEach(([name, v]) => {
    const bar = document.getElementById(`sidebar-bar-${name}`);
    const val = document.getElementById(`sidebar-tokens-${name}`);
    if (bar) bar.style.width = Math.round(v.tokens / maxTok * 100) + "%";
    if (val) val.textContent = v.tokens > 0 ? fmtTokens(v.tokens) : "—";
  });

  _lastSnapshot = { total_tokens, total_cost_usd };
}

function markTaskStart() {
  _taskStart = { ..._lastSnapshot };
  _taskStartTime = Date.now();

  // Reset current-task display
  _set("task-delta-tok",  "—");
  _set("task-delta-cost", "—");
  _set("task-dur", "0s");

  // Hide last-task row
  const row = document.getElementById("task-delta");
  if (row) row.style.display = "none";

  // Live duration ticker
  clearInterval(_taskTimerHandle);
  _taskTimerHandle = setInterval(_tickTaskDuration, 1000);
}

function _tickTaskDuration() {
  if (!_taskStartTime) return;
  const secs = Math.floor((Date.now() - _taskStartTime) / 1000);
  const dur = secs >= 60
    ? `${Math.floor(secs / 60)}m ${secs % 60}s`
    : `${secs}s`;
  _set("task-dur", dur);

  const dt = _lastSnapshot.total_tokens - _taskStart.total_tokens;
  const dc = _lastSnapshot.total_cost_usd - _taskStart.total_cost_usd;
  if (dt > 0) {
    _set("task-delta-tok",  `+${fmtTokens(dt)}`);
    _set("task-delta-cost", `+${fmtCost(dc)}`);
  }
}

function markTaskEnd() {
  clearInterval(_taskTimerHandle);
  _taskTimerHandle = null;

  const dt = _lastSnapshot.total_tokens - _taskStart.total_tokens;
  const dc = _lastSnapshot.total_cost_usd - _taskStart.total_cost_usd;
  const secs = _taskStartTime ? Math.floor((Date.now() - _taskStartTime) / 1000) : 0;
  const dur = secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`;

  _set("task-dur", dur);
  if (dt > 0) {
    _set("task-delta-tok",  `+${fmtTokens(dt)}`);
    _set("task-delta-cost", `+${fmtCost(dc)}`);
  }
}

window.ArgusDashboard = {
  update:    updateDashboard,
  taskStart: markTaskStart,
  taskEnd:   markTaskEnd,
};
