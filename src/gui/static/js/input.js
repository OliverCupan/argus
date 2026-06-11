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

  function _unblockInput() {
    _busy = false;
    sendBtn.disabled = false;
  }

  // Expose unblock so external callers (e.g. WebSocket task_complete handler) can
  // unlock the input immediately without waiting for the HTTP fetch to settle.
  window.ArgusInput.unblock = _unblockInput;

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
      _unblockInput();
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
      // Must go inside the timeline-section so it sits in the correct grid area
      const feed = document.getElementById("timeline-feed");
      if (feed) feed.parentElement.insertBefore(panel, feed);
      else document.querySelector(".timeline-section")?.prepend(panel);
    }
    const close = () => { panel.style.display = "none"; };
    // Ensure panel is visible when new result arrives
    panel.style.display = "";
    const content = window.marked
      ? `<div class="md-render">${window.marked.parse(markdown)}</div>`
      : `<pre style="white-space:pre-wrap">${markdown}</pre>`;
    panel.innerHTML =
      `<div class="result-panel-header">` +
        `<span class="result-panel-title">Result</span>` +
        `<button class="result-close-btn" aria-label="Close" title="Close (Esc)">&times;</button>` +
      `</div>` +
      `<div class="result-panel-body">${content}</div>`;
    panel.querySelector(".result-close-btn").addEventListener("click", close);
    // Scroll to top of panel on new result
    panel.scrollTop = 0;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Escape key closes the panel while it is visible
    function _onKey(e) {
      if (e.key === "Escape" && panel.style.display !== "none") {
        close();
        document.removeEventListener("keydown", _onKey);
      }
    }
    document.removeEventListener("keydown", _onKey); // clear any stale listener
    document.addEventListener("keydown", _onKey);
  }
}

window.ArgusInput = { init: initInput };
