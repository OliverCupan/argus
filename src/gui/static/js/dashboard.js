/* ================================================================
   dashboard.js — Token / cost gauges and budget progress bars
   ================================================================ */

let _lastSnapshot = { total_tokens: 0, total_cost_usd: 0 };
let _taskStart    = { total_tokens: 0, total_cost_usd: 0 };
let _inTask = false;

function fmtTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function fmtCost(n) {
  return "$" + n.toFixed(4);
}

function updateDashboard(summary) {
  if (!summary) return;

  const { total_tokens, total_cost_usd, hard_cap, dollar_hard_cap } = summary;

  // Token gauge
  const tokenPct = hard_cap > 0 ? Math.min(100, total_tokens / hard_cap * 100) : 0;
  const tokenFill = document.getElementById("token-fill");
  const tokenVal  = document.getElementById("token-value");
  if (tokenFill) {
    tokenFill.style.width = tokenPct + "%";
    tokenFill.classList.toggle("warn",   tokenPct >= 70 && tokenPct < 90);
    tokenFill.classList.toggle("danger", tokenPct >= 90);
  }
  if (tokenVal) {
    tokenVal.textContent = `${fmtTokens(total_tokens)} / ${fmtTokens(hard_cap)}`;
  }

  // Cost gauge
  const costPct  = dollar_hard_cap > 0 ? Math.min(100, total_cost_usd / dollar_hard_cap * 100) : 0;
  const costFill = document.getElementById("cost-fill");
  const costVal  = document.getElementById("cost-value");
  if (costFill) {
    costFill.style.width = costPct + "%";
    costFill.classList.toggle("warn",   costPct >= 70 && costPct < 90);
    costFill.classList.toggle("danger", costPct >= 90);
  }
  if (costVal) {
    costVal.textContent = `${fmtCost(total_cost_usd)} / $${dollar_hard_cap.toFixed(2)}`;
  }

  _lastSnapshot = { total_tokens, total_cost_usd };
}

function markTaskStart() {
  _taskStart = { ..._lastSnapshot };
  _inTask = true;
  const delta = document.getElementById("task-delta");
  if (delta) {
    delta.textContent = "";
    delta.classList.remove("visible");
  }
}

function markTaskEnd() {
  _inTask = false;
  const dt = _lastSnapshot.total_tokens - _taskStart.total_tokens;
  const dc = _lastSnapshot.total_cost_usd - _taskStart.total_cost_usd;
  const delta = document.getElementById("task-delta");
  if (delta && dt > 0) {
    delta.textContent = `Task: +${fmtTokens(dt)} / ${fmtCost(dc)}`;
    delta.classList.add("visible");
  }
}

window.ArgusDashboard = {
  update: updateDashboard,
  taskStart: markTaskStart,
  taskEnd: markTaskEnd,
};
