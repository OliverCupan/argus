---
date: 2026-06-11T00:00:00
planner: GIVERNY
research_doc: thoughts/shared/research/2026-06-11-compaction-manual-and-visual-verify.md
status: complete
iteration: 1
---

# Plan: Manual Compact Trigger + Visual Verification

## Objective
When auto-compaction fires during an agent run, a `compaction` event flows through the EventBus to the frontend and renders as a distinct visual badge in the timeline. A `/compact` command (and `POST /api/compact` endpoint) lets the user force an aggressive history trim mid-run.

## Architectural note
`messages` is local to each `agent_loop.py:BaseAgent.run()` call — there is no persistent cross-run history. Manual compact is only meaningful DURING an active run. Between runs, a `compact` command emits an acknowledgement event showing context is already fresh.

## Research Reference
Based on: `thoughts/shared/research/2026-06-11-compaction-manual-and-visual-verify.md`

---

## Phase 1: Backend — Compaction Event Emission

### What
Add an optional `emit_fn` callback to `ContextManager`. Wire it in `BaseAgent.__init__()` so every auto-compact (tool output tiers 2–4) and every `trim_history()` drop emits a `compaction` EventBus event.

### Sandbox
Files to modify:
- `src/gui/event_bus.py` — add `COMPACTION = "compaction"` constant
- `src/core/context_manager.py` — accept optional `emit_fn: Callable | None` in `__init__`, call it in `_summarize()` and `trim_history()` when trimming actually occurs
- `src/core/agent_loop.py` — pass `emit_fn=self._emit` (bound method) when constructing `self.context = ContextManager(...)`

Files to read (reference only):
- `src/gui/event_bus.py` (existing constants pattern)
- `src/core/agent_loop.py:75-81` (`_emit` method signature)

### Event payload
```
event_type: "compaction"
agent_name: <agent>
data: {
  kind: "tool_output" | "history_trim",
  tier: 2 | 3 | 4 | null,          # tool_output only
  messages_dropped: int | null,      # history_trim only
  tokens_saved_est: int,
}
```

### Success Criteria
- [ ] `ContextManager.__init__` accepts `emit_fn=None` without breaking existing callers
- [ ] `_summarize()` calls `emit_fn` with kind=`tool_output`, tier, estimated tokens saved
- [ ] `trim_history()` calls `emit_fn` with kind=`history_trim`, messages_dropped count (only when drop_idx is non-empty)
- [ ] `BaseAgent.__init__` passes `emit_fn` to ContextManager
- [ ] All existing tests in `tests/test_argus.py:354-459` still pass

### Verification
Automated:
- [ ] `pytest tests/test_argus.py` passes

---

## Phase 2: Backend — Manual Compact Trigger

### What
Add `_compact_requested: asyncio.Event` to `BaseAgent`. The run loop checks it after each trim — if set, forces an aggressive trim (50% of normal budget) and clears the flag. Expose via `Orchestrator.request_compact()`, `gui_app.py` command handler, and `POST /api/compact` endpoint.

### Sandbox
Files to modify:
- `src/core/agent_loop.py` — add `_compact_requested: asyncio.Event` to `BaseAgent.__init__`, check after `trim_history()` call in main loop; also add `request_compact()` public method on `BaseAgent`
- `src/agents/orchestrator.py` — add `request_compact()` method: calls `agent.request_compact()` on all agents (explorer, challenger, coder, auditors)
- `src/gui/gui_app.py` — add `compact` to `handle_command()`: calls `self.orchestrator.request_compact()`, emits `compaction` event via event_bus with kind=`manual_requested`, returns status string
- `src/gui/server.py` — add `POST /api/compact` endpoint that calls `gui.handle_compact()`; add `handle_compact()` to `GuiApp`

Files to read (reference only):
- `src/gui/gui_app.py:108-159` (handle_command pattern)
- `src/gui/server.py:177-213` (endpoint pattern)
- `src/core/agent_loop.py:307-308` (trim_history call site to patch)

### Success Criteria
- [ ] `POST /api/compact` returns `{"ok": true}` without error
- [ ] `compact` typed in GUI command box returns a status message
- [ ] If no run is active: returns "No active run — context is fresh" (no crash)
- [ ] If run IS active: `_compact_requested` is set, loop applies aggressive trim next iteration, emits `compaction` event with kind=`manual`
- [ ] All existing tests pass

### Verification
Automated:
- [ ] `pytest tests/` passes

Manual (DEV must verify):
- [ ] Type `compact` in GUI command box — confirm response message appears
- [ ] `curl -X POST http://localhost:8000/api/compact` returns 200

---

## Phase 3: Frontend — Visual Compaction Badge

### What
`timeline.js` renders `compaction` events as a distinct horizontal badge row (not a normal event row). `index.html` gets a "Compact" button in the toolbar that POSTs to `/api/compact`. `app.js` routes `compaction` events to a toast notification for immediate visibility.

### Sandbox
Files to modify:
- `src/gui/static/js/timeline.js` — special-case `event_type === "compaction"` in `add()`: render a badge row with icon, kind label, and token savings
- `src/gui/static/js/app.js` — in `_dispatch()`: handle `compaction` event → show a brief toast/flash notification
- `src/gui/static/index.html` — add "⚡ Compact" button to toolbar; wire click → `POST /api/compact`

Files to read (reference only):
- `src/gui/static/js/app.js:36-70` (dispatch routing pattern)
- `src/gui/static/index.html` (toolbar/button structure)
- `src/gui/static/js/timeline.js` (existing event rendering)

### Visual spec
Timeline badge:
```
┌──────────────────────────────────────────────────────────────┐
│  ⚡  Context compacted · tool_output tier-3 · ~800 tokens saved │
└──────────────────────────────────────────────────────────────┘
```
- Background: `#2a1a4a` (purple tint, distinct from normal event rows)
- Text: `#c084fc` (light purple)
- Full-width horizontal separator above + below
- No expand/collapse (it's a marker, not a log entry)

Toast: 2-second flash in top-right corner, same purple palette.

Compact button: placed next to the Stats button in toolbar, labeled "⚡ Compact", same style as existing toolbar buttons.

### Success Criteria
- [ ] Auto-compaction during a run appears as purple badge row in timeline
- [ ] Badge shows kind, tier (if tool_output), and estimated tokens saved
- [ ] Toast notification flashes for 2s on any compaction event
- [ ] "⚡ Compact" button visible in toolbar
- [ ] Clicking button POSTs to `/api/compact` and shows toast response
- [ ] Existing timeline events unaffected

### Verification
Manual (DEV must verify):
- [ ] Run a task with a large tool output — confirm badge appears in timeline
- [ ] Click compact button — confirm toast appears

---

## Phase 4: Tests

### What
Add unit tests for: emit_fn callback fires correctly, `request_compact()` sets the flag, endpoint returns 200.

### Sandbox
Files to modify:
- `tests/test_argus.py` — add tests for `ContextManager(emit_fn=...)` callback invocation in `_summarize()` and `trim_history()`

Files to read (reference only):
- `tests/test_argus.py:354-459` (existing trim_history tests as pattern)

### Success Criteria
- [ ] Test: `emit_fn` called with `kind="tool_output"` when tier 2/3/4 fires
- [ ] Test: `emit_fn` called with `kind="history_trim"` only when messages actually dropped
- [ ] Test: `emit_fn` NOT called when Tier 1 (no compaction)
- [ ] All existing tests still pass

---

## Rollback Plan
If things break:
1. Revert `context_manager.py` — remove `emit_fn` param (default None already makes it backward-compatible)
2. Revert `agent_loop.py` — remove `_compact_requested` and pass-through for `emit_fn`
3. Revert `server.py` / `gui_app.py` — remove `/api/compact` endpoint
4. Frontend changes are purely additive — removing badge CSS + JS reverts cleanly

## Open Questions
- [ ] Should `trim_history()` compaction badge show which messages were dropped (e.g. "3 assistant messages trimmed")?
- [ ] Should the toast auto-dismiss, or require user click?
- [ ] Should manual compact use 50% of normal budget, or a configurable value?
