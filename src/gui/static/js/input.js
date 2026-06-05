/* ================================================================
   input.js — Command textarea with auto-resize, history, hints, send
   ================================================================ */

const COMMANDS = ["audit", "fix", "model", "budget", "stats", "exit"];
const HINTS = {
  audit:  "audit <path>   — run security/bug/perf/test scan",
  fix:    "fix <n>        — apply fix for audit finding #n",
  model:  "model [agent] [name]   — view or change agent models",
  budget: "budget [set field value]   — view or adjust limits",
  stats:  "stats          — show per-agent token usage",
};

let _history = JSON.parse(localStorage.getItem("argus_history") || "[]");
let _histIdx = -1;
let _busy = false;

function _saveHistory(cmd) {
  _history = [cmd, ..._history.filter(x => x !== cmd)].slice(0, 50);
  localStorage.setItem("argus_history", JSON.stringify(_history));
}

function initInput() {
  const textarea = document.getElementById("cmd-input");
  const sendBtn  = document.getElementById("send-btn");
  const hint     = document.getElementById("cmd-hint");

  function _autoResize() {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
  }

  function _showHint(text) {
    if (!hint) return;
    if (text) {
      hint.textContent = text;
      hint.classList.add("visible");
    } else {
      hint.classList.remove("visible");
    }
  }

  textarea.addEventListener("input", () => {
    _autoResize();
    _histIdx = -1;

    const val = textarea.value.trimStart().toLowerCase();
    const matched = COMMANDS.find(c => val.startsWith(c));
    _showHint(matched ? HINTS[matched] : null);
  });

  textarea.addEventListener("keydown", e => {
    // Send: Ctrl+Enter
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault();
      _doSend();
      return;
    }

    // History navigation: ArrowUp / ArrowDown
    if (e.key === "ArrowUp" && !e.shiftKey) {
      e.preventDefault();
      if (_histIdx < _history.length - 1) {
        _histIdx++;
        textarea.value = _history[_histIdx];
        _autoResize();
      }
    }
    if (e.key === "ArrowDown" && !e.shiftKey) {
      e.preventDefault();
      if (_histIdx > 0) {
        _histIdx--;
        textarea.value = _history[_histIdx];
      } else {
        _histIdx = -1;
        textarea.value = "";
      }
      _autoResize();
    }
  });

  sendBtn.addEventListener("click", _doSend);

  async function _doSend() {
    if (_busy) return;
    const text = textarea.value.trim();
    if (!text) return;

    _busy = true;
    sendBtn.disabled = true;
    _saveHistory(text);
    _histIdx = -1;
    _showHint(null);
    textarea.value = "";
    _autoResize();

    // Notify dashboard that a task is starting
    window.ArgusDashboard?.taskStart();
    window.ArgusAgents?.resetAll();
    // Mark the orchestrator as running immediately — it will stay that way
    // until the task_complete event arrives via WebSocket.
    window.ArgusAgents?.setStatus("orchestrator", "running");

    try {
      const res = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const json = await res.json();
      if (json.result) {
        _displayResult(json.result);
      }
    } catch (err) {
      _displayResult(`**Error:** ${err.message}`);
    } finally {
      _busy = false;
      sendBtn.disabled = false;
      window.ArgusDashboard?.taskEnd();
    }
  }

  function _displayResult(markdown) {
    // Find or create a result panel above the timeline
    let panel = document.getElementById("result-panel");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "result-panel";
      panel.className = "result-panel";
      document.querySelector(".timeline-section")
        ?.insertAdjacentElement("beforebegin", panel);
    }
    if (window.marked) {
      panel.innerHTML = `<div class="md-render">${window.marked.parse(markdown)}</div>`;
    } else {
      panel.innerHTML = `<pre style="white-space:pre-wrap">${markdown}</pre>`;
    }
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

window.ArgusInput = { init: initInput };
