---
date: 2026-06-05T00:00:00
planner: GIVERNY
research_doc: thoughts/shared/research/2026-06-05-gui-history-modal-layout.md
status: approved
iteration: 1
---

# Plan: GUI History, Modal Fix & Layout Restructure

## Objective
The GUI will have a two-column layout (left: agents + timeline strip; right: output panel with session history), the confirm modal will never push buttons off-screen, and every completed task result will be accessible by clicking its prompt in the history list.

## Research Reference
Based on: `thoughts/shared/research/2026-06-05-gui-history-modal-layout.md`

---

## Phase 1: Fix Confirm Modal Overflow Bug

**What:** The `.modal-card` and `.modal-cmd` elements have no height cap or scroll. A long command expands the card off-screen, hiding the Approve/Deny buttons. Add `max-height` + `overflow-y: auto` to both.

### Sandbox
Files to modify:
- `src/gui/static/css/main.css` — add max-height and overflow to `.modal-card` and `.modal-cmd`

### Success Criteria
- [ ] `.modal-card` has `max-height` capped (e.g. `80vh`) and `overflow-y: auto`
- [ ] `.modal-cmd` has `max-height` capped (e.g. `260px`) and `overflow-y: auto`
- [ ] Approve and Deny buttons are always visible regardless of command length
- [ ] Short commands render identically to before

### Verification
Manual (DEV must verify):
- [ ] Open confirm modal with a very long multi-line command — both buttons visible without scrolling the page

---

## Phase 2: Layout Restructure — HTML + CSS

**What:** Replace the current single-column vertical grid with a two-column layout. The left column contains the agents grid + timeline strip stacked vertically. The right column is a new output section (same height). The input bar stays full-width at the bottom.

**New grid:**
```
topbar   topbar     ← 62px, spans both columns
left     output     ← 1fr, fills remaining height
input    input      ← 80px, spans both columns
```

The `left` column is a flex-column wrapper containing:
- `.agents-grid` (flex, shrinks to fit content)
- `.timeline-section` (fixed height strip, same 220px)

The `output` section is a new `<section class="output-section" id="output-section">` containing:
- `.history-list` div — will hold clickable prompt history items (populated by Phase 3 JS)
- `.result-display` div — will hold the rendered markdown result (populated by Phase 3 JS)

Agent cards must be made smaller to fit the narrower left column:
- `--card-min-w` reduced from `160px` → `110px`
- `.agent-card` `max-width` reduced from `280px` → `200px`
- `#card-orchestrator` `max-width` reduced from `320px` → `220px`
- `.card-output` `max-height` reduced from `160px` → `100px`
- Card font sizes and padding reduced proportionally

### Sandbox
Files to modify:
- `src/gui/static/index.html` — wrap agents-grid + timeline-section in `<div class="left-col">`, add `<section class="output-section" id="output-section">` with `.history-list` and `.result-display` children
- `src/gui/static/css/main.css` — update body grid to 2 columns/3 rows; add `.left-col`, `.output-section`, `.history-list`, `.result-display` CSS rules; update `--card-min-w`
- `src/gui/static/css/agents.css` — reduce `.agent-card` max-width, `#card-orchestrator` max-width, `.card-output` max-height, padding/font-size

### Success Criteria
- [ ] Body grid is 2-column: left half = agents + timeline, right half = output section
- [ ] Left and right columns are equal width (each `1fr`)
- [ ] `.left-col` is a flex column; agents-grid is at top, timeline-section is at bottom (fixed 220px height)
- [ ] `.output-section` fills the full right half height between topbar and input
- [ ] `.history-list` and `.result-display` exist as children of `.output-section` in the DOM
- [ ] Input bar still spans full width
- [ ] Agent cards are visibly smaller than before — fit comfortably within half-screen width
- [ ] Page does not overflow horizontally on a standard 1280px screen

### Verification
Manual (DEV must verify):
- [ ] At 1280px wide: left and right halves are equal, agents fit without horizontal scroll
- [ ] At 900px wide: layout gracefully degrades (single column or wraps acceptably)

---

## Phase 3: Task History + Output Panel JS

**What:** Wire up the new output section with in-memory session history. Each completed task pushes a `{id, prompt, result, timestamp}` entry. The history list renders the prompt as a clickable item. Clicking shows the result markdown in `.result-display`. The most recent result auto-displays on completion.

Create a new `output.js` module that owns this logic entirely. Update `input.js` to call into it instead of the old `_displayResult()`. Add the script tag to `index.html`.

**`output.js` public API (exposed as `window.ArgusOutput`):**
- `ArgusOutput.push(prompt, result)` — appends to history array, renders new history item, auto-displays result
- `ArgusOutput.show(result)` — renders markdown into `.result-display` (used when clicking history items)
- `ArgusOutput.clear()` — clears history (optional, for future use)

**History item render:** each item is a `<div class="history-item">` containing the truncated prompt text (max 60 chars) and a timestamp. Active item gets `.active` class. Clicking sets active + calls `ArgusOutput.show(result)`.

**Result display:** uses `marked.parse(result)` (already loaded via CDN) to render markdown into `.result-display`.

### Sandbox
Files to modify:
- `src/gui/static/js/input.js` — replace `_displayResult(markdown)` body: call `window.ArgusOutput?.push(_lastPrompt, markdown)` instead of the inline DOM manipulation; store the submitted prompt in `_lastPrompt` at send time
- `src/gui/static/index.html` — add `<script src="/static/js/output.js"></script>` before `app.js`

Files to create:
- `src/gui/static/js/output.js` — full implementation of ArgusOutput module

### Success Criteria
- [ ] `window.ArgusOutput` is defined with `push`, `show`, `clear`
- [ ] Submitting a task and receiving a result adds a new item to `.history-list`
- [ ] The most recent result is automatically displayed in `.result-display` on task completion
- [ ] Clicking a history item displays its stored result in `.result-display`
- [ ] The clicked item receives `.active` class; others lose it
- [ ] History is in-memory only — refreshing the page clears it
- [ ] Prompt text in history items is truncated to 60 chars with ellipsis
- [ ] Markdown renders correctly (uses `marked.parse`)
- [ ] `input.js` no longer creates `#result-panel` directly

### Verification
Manual (DEV must verify):
- [ ] Submit two different tasks — both appear in history list
- [ ] Click first task — its result renders in output panel
- [ ] Click second task — its result replaces the first in output panel
- [ ] Refresh page — history list is empty (session-only confirmed)

---

## Rollback Plan
1. All changes confined to 5 files + 1 new file
2. `git checkout src/gui/static/index.html src/gui/static/css/main.css src/gui/static/css/agents.css src/gui/static/js/input.js` restores HTML/CSS/input.js
3. `git rm src/gui/static/js/output.js` removes the new module

---

## Open Questions
- None — all DEV decisions received.
