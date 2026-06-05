/* ================================================================
   confirm.js — Approval modal for REVIEW bash commands
   ================================================================ */

let _countdownTimer = null;
let _approveHandler = null;
let _denyHandler    = null;

function showConfirm(requestId, command) {
  const overlay = document.getElementById("confirm-overlay");
  const cmdEl   = document.getElementById("confirm-cmd");
  const countEl = document.getElementById("confirm-countdown");
  if (!overlay || !cmdEl) return;

  cmdEl.textContent = command;
  overlay.classList.remove("hidden");

  let seconds = 60;
  countEl.textContent = seconds;

  _countdownTimer = setInterval(() => {
    seconds--;
    countEl.textContent = seconds;
    if (seconds <= 0) _respond(requestId, false);
  }, 1000);

  const approveBtn = document.getElementById("confirm-approve");
  const denyBtn    = document.getElementById("confirm-deny");

  // Remove any stale listeners from a previous (timed-out) confirm
  if (_approveHandler) approveBtn?.removeEventListener("click", _approveHandler);
  if (_denyHandler)    denyBtn?.removeEventListener("click", _denyHandler);

  _approveHandler = () => _respond(requestId, true);
  _denyHandler    = () => _respond(requestId, false);

  approveBtn?.addEventListener("click", _approveHandler, { once: true });
  denyBtn?.addEventListener("click", _denyHandler, { once: true });
}

async function _respond(requestId, approved) {
  clearInterval(_countdownTimer);
  document.getElementById("confirm-overlay")?.classList.add("hidden");

  // Clean up handlers so they cannot fire again after modal is hidden
  const approveBtn = document.getElementById("confirm-approve");
  const denyBtn    = document.getElementById("confirm-deny");
  if (_approveHandler) approveBtn?.removeEventListener("click", _approveHandler);
  if (_denyHandler)    denyBtn?.removeEventListener("click", _denyHandler);
  _approveHandler = null;
  _denyHandler    = null;

  try {
    await fetch(`/api/confirm/${requestId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
  } catch (err) {
    console.error("Confirm response failed:", err);
  }
}

window.ArgusConfirm = { show: showConfirm };
