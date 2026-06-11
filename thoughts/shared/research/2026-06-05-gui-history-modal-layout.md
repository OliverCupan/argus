---
date: 2026-06-05T00:00:00
researcher: GIVERNY
git_commit: 11d0d308a142a33f15267905226f0011cdaad25b
branch: master
topic: "Task history persistence, approve/reject modal overflow bug, GUI layout restructure"
status: complete
---

# Research: GUI History, Modal Bug & Layout Restructure

## Summary
The GUI has no task output history — each result overwrites the previous one in a single DOM panel, and nothing is persisted server-side. Prompt input history already exists in localStorage (up to 50 entries). The confirm modal has a well-structured backend flow but no `max-height` or scroll on its content, so long commands push the buttons off-screen. The current layout is a single-column vertical grid with a hardcoded 220px timeline strip at the bottom — no dedicated output panel exists, though dead `.result-panel` CSS is already defined but unused.

---

## File Locations

### History — Backend
- `src/gui/gui_app.py:108` — `handle_command()` — receives task, executes, returns result string. Result never stored.
- `src/gui/gui_app.py:152` — `result = await self.orchestrator.handle(...)` — assigned to local var only.
- `src/gui/server.py:178-182` — `POST /api/command` — returns `{"result": result}`, no logging or storage.
- **No history endpoint exists.** No `GET /api/history` route.

### History — Frontend
- `src/gui/static/js/input.js:14` — `_history` array loaded from `localStorage["argus_history"]` (up to 50 prompts). **Input history only** — not task output.
- `src/gui/static/js/input.js:19-20` — `_saveHistory(cmd)` — prepends, deduplicates, caps at 50, writes back.
- `src/gui/static/js/input.js:91` — `_saveHistory(text)` called on every send.
- `src/gui/static/js/input.js:123-138` — `_displayResult(markdown)` — creates/reuses single `<div id="result-panel">`. **Overwrites on every task.** No accumulation.
- `src/gui/static/js/app.js:67-69` — `task_complete` WebSocket handler — calls `taskEnd()` only. No content capture.
- `src/gui/static/js/dashboard.js:63-72` — `markTaskEnd()` — records token/cost delta only.

### Modal — HTML
- `src/gui/static/index.html:108` — `<div class="modal-overlay hidden" id="confirm-overlay">`
- `src/gui/static/index.html:109` — `<div class="modal-card" id="confirm-card">`
- `src/gui/static/index.html:118` — `<pre class="modal-cmd" id="confirm-cmd">` — command display (no scroll)
- `src/gui/static/index.html:119` — `<div class="modal-countdown" id="confirm-countdown">` — 60s auto-deny timer
- `src/gui/static/index.html:120-123` — `.modal-actions` with `id="confirm-deny"` and `id="confirm-approve"` buttons

### Modal — CSS (bug location)
- `src/gui/static/css/main.css:379-387` — `.modal-card`: `max-width: 520px; width: 90%; padding: 24px` — **NO `max-height`, NO `overflow`**
- `src/gui/static/css/main.css:405-416` — `.modal-cmd`: `white-space: pre-wrap; word-break: break-all` — **NO `max-height`, NO `overflow-y`**
- `src/gui/static/css/main.css:424` — `.modal-actions: justify-content: flex-end`

### Modal — JS
- `src/gui/static/js/confirm.js:9` — `showConfirm(requestId, command)` — show function
- `src/gui/static/js/confirm.js:15` — `cmdEl.textContent = command` — content injection
- `src/gui/static/js/confirm.js:16` — `overlay.classList.remove("hidden")` — show
- `src/gui/static/js/confirm.js:18-25` — 60s countdown; auto-deny on expiry
- `src/gui/static/js/confirm.js:41` — `_respond(requestId, approved)` — shared handler
- `src/gui/static/js/confirm.js:43` — `overlay.classList.add("hidden")` — hide
- `src/gui/static/js/confirm.js:54-58` — `POST /api/confirm/${requestId}` with `{ approved }`
- `src/gui/static/js/app.js:62-64` — `confirm_required` WebSocket → `ArgusConfirm.show()`

### Modal — Backend
- `src/gui/server.py:184-189` — `POST /api/confirm/{request_id}` → `gui.resolve_confirm()`
- `src/gui/gui_app.py:54` — `self._pending_confirms` — transient dict for in-flight confirms

### Layout — Current Structure
- `src/gui/static/css/main.css:63-71` — Body grid: `grid-template-rows: 62px 1fr 220px 80px` → areas: `topbar / agents / timeline / input`
- `src/gui/static/css/main.css:33-35` — CSS vars: `--topbar-h: 62px`, `--input-h: 80px`, `--card-min-w: 160px`
- `src/gui/static/css/main.css:65` — Timeline hardcoded `220px`. Agents row `1fr`.

### Layout — Timeline Panel
- `src/gui/static/index.html:65` — `<section class="timeline-section">`
- `src/gui/static/index.html:81` — `<div class="timeline-feed" id="timeline-feed">` — scrollable event list
- `src/gui/static/css/main.css:222-229` — `.timeline-section`: `grid-area: timeline; overflow: hidden`
- `src/gui/static/css/main.css:260-264` — `.timeline-feed`: `overflow-y: auto`
- `src/gui/static/js/timeline.js:104-120` — `addTimelineEntry()` — appends rows, auto-scrolls

### Layout — Output Area (currently absent as standalone panel)
- `src/gui/static/js/agents.js:250-264` — `task_complete` appends `.output-summary` into orchestrator card's `.card-output` div — no dedicated panel
- `src/gui/static/js/timeline.js:83-84` — `task_complete` in `_buildRow()`: `data.result_markdown` rendered as collapsed `.tl-detail` inside timeline (capped 1000 chars)
- `src/gui/static/css/main.css:473-483` — `.result-panel` CSS exists (`max-height: 240px`) but **no element uses it in index.html** — dead code

### Layout — Agent Cards
- `src/gui/static/index.html:60-62` — `<section class="agents-grid" id="agents-grid">`
- `src/gui/static/css/main.css:202-210` — `.agents-grid`: `display: flex; overflow-x: auto; align-items: stretch`
- `src/gui/static/css/agents.css:6-22` — `.agent-card`: `flex: 1 1 160px; max-width: 280px`
- `src/gui/static/css/agents.css:25-28` — `#card-orchestrator`: `max-width: 320px`
- `src/gui/static/js/agents.js:5-14` — `AGENTS` array: 8 agents defined
- `src/gui/static/js/agents.js:91-118` — `_buildCard()` — card HTML structure

### Layout — Input Bar
- `src/gui/static/index.html:87-105` — `<footer class="input-area">`
- `src/gui/static/css/main.css:274-282` — `.input-area`: `grid-area: input`
- `src/gui/static/css/main.css:289-304` — `.cmd-textarea`: `max-height: 120px`

---

## How It Works

### Current Layout (grid)
```
┌─────────────────────────────┐  62px
│           topbar            │
├─────────────────────────────┤  1fr (fills remaining)
│     agents-grid (cards)     │
├─────────────────────────────┤  220px hardcoded
│       timeline-feed         │
├─────────────────────────────┤  80px
│         input-area          │
└─────────────────────────────┘
```
All sections span full width. There is no horizontal split anywhere.

### Task Output Flow
User submits → `POST /api/command` → orchestrator runs → result returned in HTTP response → `_displayResult()` injects markdown into single `#result-panel` div appended inside `<main>`. The previous result is destroyed. No history kept.

### Prompt Input History
Works via `_history[]` in localStorage. Arrow keys cycle through previous commands. Persists across page reloads. Already functional.

---

## Patterns Observed

| # | Pattern | Location |
|---|---------|----------|
| 1 | Modal card has no `max-height` | `main.css:379-387` |
| 2 | Modal command block has no scroll | `main.css:405-416` |
| 3 | Task result overwrites single DOM panel | `input.js:123-138` |
| 4 | No task history array anywhere | — |
| 5 | Prompt history in localStorage (works) | `input.js:14` |
| 6 | Dead `.result-panel` CSS unused | `main.css:473` |
| 7 | No horizontal split in layout | `main.css:63-71` |
| 8 | Output buried inside orchestrator card | `agents.js:250-264` |
| 9 | `task_complete` double-fires `taskEnd()` | `input.js:119` + `app.js:67` |

---

## Open Questions
- Should task history (stored results) persist across page reloads (localStorage/IndexedDB), or only within the current session (in-memory array)?
- Should the history sidebar show prompt + result pairs, or prompts only with results on click?
- Should the right-side output panel replace the current orchestrator card output, or be additive?
- The timeline is currently 220px tall at the bottom. In the new layout it moves to the left half — should it keep its height-based scroll or become a full-height column?
