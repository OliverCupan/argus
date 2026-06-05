/* ================================================================
   agents.js — Agent card rendering, SVG avatars, state machine
   ================================================================ */

const AGENTS = [
  { name: "orchestrator",        label: "Overseer",      color: "#e2e8f0", colorDim: "rgba(226,232,240,0.12)" },
  { name: "explorer",            label: "Explorer",      color: "#3b82f6", colorDim: "rgba(59,130,246,0.12)"  },
  { name: "challenger",          label: "Challenger",    color: "#f59e0b", colorDim: "rgba(245,158,11,0.12)"  },
  { name: "coder",               label: "Coder",         color: "#22c55e", colorDim: "rgba(34,197,94,0.12)"   },
  { name: "security_auditor",    label: "Security",      color: "#ef4444", colorDim: "rgba(239,68,68,0.12)"   },
  { name: "bug_auditor",         label: "Bugs",          color: "#eab308", colorDim: "rgba(234,179,8,0.12)"   },
  { name: "performance_auditor", label: "Performance",   color: "#a855f7", colorDim: "rgba(168,85,247,0.12)"  },
  { name: "test_auditor",        label: "Tests",         color: "#06b6d4", colorDim: "rgba(6,182,212,0.12)"   },
];

// Inline SVG icons per agent (40×40 viewBox)
const AGENT_SVGS = {
  orchestrator: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <ellipse cx="20" cy="20" rx="17" ry="10" stroke="currentColor" stroke-width="2"/>
      <circle cx="20" cy="20" r="5" stroke="currentColor" stroke-width="2"/>
      <circle cx="20" cy="20" r="2" fill="currentColor"/>
      <line x1="3" y1="20" x2="8" y2="20" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="32" y1="20" x2="37" y2="20" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>`,
  explorer: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="20" cy="20" r="10" stroke="currentColor" stroke-width="2"/>
      <circle cx="20" cy="20" r="3"  fill="currentColor"/>
      <line x1="20" y1="2"  x2="20" y2="10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="20" y1="30" x2="20" y2="38" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="2"  y1="20" x2="10" y2="20" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="30" y1="20" x2="38" y2="20" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>`,
  challenger: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M20 4 L34 10 L34 22 C34 30 27 36 20 38 C13 36 6 30 6 22 L6 10 Z"
            stroke="currentColor" stroke-width="2" fill="none"/>
      <line x1="14" y1="15" x2="26" y2="27" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
      <line x1="26" y1="15" x2="14" y2="27" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
    </svg>`,
  coder: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4" y="8" width="32" height="24" rx="3" stroke="currentColor" stroke-width="2"/>
      <path d="M13 16 L8 20 L13 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M27 16 L32 20 L27 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <rect class="cursor-bar" x="18" y="17" width="4" height="7" rx="1" fill="currentColor"/>
    </svg>`,
  security_auditor: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="10" y="18" width="20" height="16" rx="3" stroke="currentColor" stroke-width="2"/>
      <path d="M14 18 L14 12 A6 6 0 0 1 26 12 L26 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <circle cx="20" cy="26" r="2.5" fill="currentColor"/>
      <line x1="20" y1="28" x2="20" y2="31" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>`,
  bug_auditor: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <ellipse cx="20" cy="22" rx="8" ry="10" stroke="currentColor" stroke-width="2"/>
      <circle cx="20" cy="12" r="4" stroke="currentColor" stroke-width="2"/>
      <line x1="12" y1="18" x2="5"  y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="28" y1="18" x2="35" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="12" y1="24" x2="5"  y2="24" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="28" y1="24" x2="35" y2="24" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="12" y1="30" x2="5"  y2="34" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="28" y1="30" x2="35" y2="34" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>`,
  performance_auditor: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <polygon points="20,4 26,20 38,20 28,28 32,40 20,32 8,40 12,28 2,20 14,20"
               stroke="currentColor" stroke-width="2" fill="none"/>
    </svg>`,
  test_auditor: `
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M14 4 L14 18 L8 32 A2 2 0 0 0 10 36 L30 36 A2 2 0 0 0 32 32 L26 18 L26 4 Z"
            stroke="currentColor" stroke-width="2" fill="none"/>
      <line x1="14" y1="10" x2="26" y2="10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <path d="M14 26 L18 30 L26 22" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,
};

// Runtime state per agent
const agentState = {};

// Character count per agent output box — avoids reading textContent on every append
const _outputChars = {};

function _cardId(name) { return `card-${name}`; }
function _outputId(name) { return `output-${name}`; }
function _dotId(name)    { return `dot-${name}`; }

function _buildCard(agent) {
  const card = document.createElement("div");
  card.className = "agent-card idle";
  card.id = _cardId(agent.name);
  card.dataset.agent = agent.name;
  card.style.cssText = `--agent-color:${agent.color}; --agent-color-dim:${agent.colorDim};`;

  card.innerHTML = `
    <div class="card-header">
      <span class="card-name">${agent.label}</span>
      <span class="status-dot idle" id="${_dotId(agent.name)}"></span>
    </div>
    <div class="avatar-wrap">
      <div class="avatar-ring"></div>
      <div class="avatar-svg" style="color:${agent.color}">
        ${AGENT_SVGS[agent.name] || ""}
      </div>
      <div class="avatar-check">✓</div>
    </div>
    <div class="card-model" id="model-${agent.name}">—</div>
    <div class="card-output" id="${_outputId(agent.name)}"></div>
    <div class="card-footer">
      <span class="card-iter" id="iter-${agent.name}"></span>
      <span id="tokens-${agent.name}"></span>
    </div>
  `;
  return card;
}

function initAgentCards() {
  const grid = document.getElementById("agents-grid");
  AGENTS.forEach(a => {
    agentState[a.name] = { status: "idle", tokens_in: 0, tokens_out: 0, iteration: 0 };
    grid.appendChild(_buildCard(a));
  });
}

// ── State transitions ──────────────────────────────────────────

function setAgentState(agentName, status) {
  const card = document.getElementById(_cardId(agentName));
  const dot  = document.getElementById(_dotId(agentName));
  if (!card) return;

  card.classList.remove("idle", "running", "done", "error");
  card.classList.add(status);
  if (dot) {
    dot.classList.remove("idle", "running", "done", "error");
    dot.classList.add(status);
  }
  if (agentState[agentName]) agentState[agentName].status = status;
}

function resetAgentCard(agentName) {
  const out = document.getElementById(_outputId(agentName));
  if (out) out.textContent = "";
  _outputChars[agentName] = 0;
  const iter = document.getElementById(`iter-${agentName}`);
  if (iter) iter.textContent = "";
  const tok  = document.getElementById(`tokens-${agentName}`);
  if (tok) tok.textContent = "";
  setAgentState(agentName, "idle");
  if (agentState[agentName]) {
    Object.assign(agentState[agentName], { tokens_in: 0, tokens_out: 0, iteration: 0 });
  }
}

function resetAllCards() {
  AGENTS.forEach(a => resetAgentCard(a.name));
}

// ── Output appending ───────────────────────────────────────────

const MAX_OUTPUT_CHARS = 4000;

// Tracks the most recently opened tool-group block per agent so tool_result
// can be appended inside it.
const _lastToolGroup = {};

function _getOut(agentName) {
  return document.getElementById(_outputId(agentName));
}

function _trimOutput(agentName, out) {
  while (_outputChars[agentName] > MAX_OUTPUT_CHARS && out.firstChild) {
    _outputChars[agentName] -= out.firstChild.textContent.length;
    out.removeChild(out.firstChild);
  }
}

// Append a simple text span (for backwards-compat and agent_text)
function _appendOutput(agentName, cls, text) {
  const out = _getOut(agentName);
  if (!out) return;
  const line = text + "\n";
  _outputChars[agentName] = (_outputChars[agentName] || 0) + line.length;
  const span = document.createElement("span");
  span.className = cls;
  span.textContent = line;
  out.appendChild(span);
  _trimOutput(agentName, out);
  out.scrollTop = out.scrollHeight;
}

// Append a structured block div
function _appendBlock(agentName, cls, innerHTML) {
  const out = _getOut(agentName);
  if (!out) return null;
  const block = document.createElement("div");
  block.className = `output-block ${cls}`;
  block.innerHTML = innerHTML;
  const text = block.textContent || "";
  _outputChars[agentName] = (_outputChars[agentName] || 0) + text.length;
  out.appendChild(block);
  _trimOutput(agentName, out);
  out.scrollTop = out.scrollHeight;
  return block;
}

function _esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ── Orchestrator card helpers ──────────────────────────────────

const AGENT_LABELS = Object.fromEntries(AGENTS.map(a => [a.name, a.label]));

function _orchStatus(message) {
  _appendBlock("orchestrator", "output-status", _esc(message));
}

// ── Event handlers ─────────────────────────────────────────────

function handleAgentEvent(event) {
  const { agent_name, event_type, data } = event;

  // ── Orchestrator card: mirror child agent lifecycle events ───
  if (agent_name !== "orchestrator") {
    const label = AGENT_LABELS[agent_name] || agent_name;
    if (event_type === "agent_started") {
      _orchStatus(`${label} started`);
    } else if (event_type === "agent_finished") {
      const total = (data.tokens_in || 0) + (data.tokens_out || 0);
      const iters = data.iterations || 0;
      _orchStatus(`${label} done — ${iters} iter${iters !== 1 ? "s" : ""}, ${total.toLocaleString()} tok`);
    }
  }

  switch (event_type) {
    // ── Orchestrator-specific events ──────────────────────────
    case "status_update": {
      if (agent_name !== "orchestrator") break;
      _orchStatus(data.message || "");
      break;
    }

    case "task_complete": {
      if (agent_name !== "orchestrator") break;
      setAgentState("orchestrator", "done");
      const summary = data.summary || {};
      const totalTok = summary.total_tokens || 0;
      const tokEl = document.getElementById("tokens-orchestrator");
      if (tokEl && totalTok) tokEl.textContent = `${totalTok.toLocaleString()}tok`;
      _appendBlock(
        "orchestrator",
        "output-summary",
        `<span class="summary-check">✓</span><span class="summary-iters">task complete</span>` +
        (totalTok ? `<span class="summary-tokens">${totalTok.toLocaleString()} tok total</span>` : "")
      );
      break;
    }

    case "agent_started": {
      setAgentState(agent_name, "running");
      _lastToolGroup[agent_name] = null;
      const modelEl = document.getElementById(`model-${agent_name}`);
      if (modelEl && data.model) modelEl.textContent = data.model.replace("claude-", "");
      const preview = _esc((data.task_preview || "starting…").slice(0, 120));
      _appendBlock(agent_name, "output-task-header", `<span class="task-arrow">▶</span>${preview}`);
      break;
    }

    case "agent_iteration": {
      const iterEl = document.getElementById(`iter-${agent_name}`);
      if (iterEl) iterEl.textContent = `iter ${data.iteration}/${data.max_iterations}`;
      break;
    }

    case "tool_call": {
      const toolName = _esc(data.tool_name || "");
      const inputPreview = _esc((data.input_preview || "").slice(0, 160));
      const group = _appendBlock(
        agent_name,
        "output-tool-group",
        `<div class="output-tool-header"><span class="tool-icon">⚙</span><span class="tool-name">${toolName}</span></div>` +
        `<div class="output-tool-input">${inputPreview}</div>`
      );
      _lastToolGroup[agent_name] = group;
      break;
    }

    case "tool_result": {
      const out = _getOut(agent_name);
      if (!out) break;
      const icon    = data.is_error ? "✗" : "✓";
      const resCls  = data.is_error ? "output-result-error" : "output-result-ok";
      const dur     = data.duration_ms != null ? `<span class="tool-dur">${data.duration_ms}ms</span>` : "";
      const preview = _esc((data.result_preview || "").slice(0, 200));
      const resultHtml =
        `<div class="output-tool-result ${resCls}"><span class="result-icon">${icon}</span>${dur}${preview}</div>`;
      const group = _lastToolGroup[agent_name];
      if (group && out.contains(group)) {
        const resultEl = document.createElement("div");
        resultEl.className = `output-tool-result ${resCls}`;
        resultEl.innerHTML = `<span class="result-icon">${icon}</span>${dur}${preview}`;
        group.appendChild(resultEl);
        _outputChars[agent_name] = (_outputChars[agent_name] || 0) + (resultEl.textContent || "").length;
      } else {
        _appendBlock(agent_name, `output-tool-group`, resultHtml);
      }
      out.scrollTop = out.scrollHeight;
      break;
    }

    case "agent_text": {
      if (!data.content) break;
      const text = data.content.slice(0, 400);
      _lastToolGroup[agent_name] = null;
      _appendBlock(agent_name, "output-thinking", _esc(text));
      break;
    }

    case "agent_finished": {
      setAgentState(agent_name, "done");
      const total = (data.tokens_in || 0) + (data.tokens_out || 0);
      const tokEl = document.getElementById(`tokens-${agent_name}`);
      if (tokEl) tokEl.textContent = `${total.toLocaleString()}tok`;
      const iters = data.iterations || 0;
      _appendBlock(
        agent_name,
        "output-summary",
        `<span class="summary-check">✓</span>` +
        `<span class="summary-iters">${iters} iter${iters !== 1 ? "s" : ""}</span>` +
        `<span class="summary-tokens">${total.toLocaleString()} tok</span>`
      );
      break;
    }
  }
}

// Export public API
window.ArgusAgents = {
  init: initAgentCards,
  handleEvent: handleAgentEvent,
  resetAll: resetAllCards,
  setStatus: setAgentState,
  AGENTS,
};
