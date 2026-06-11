---
date: 2026-06-11T00:00:00
researcher: GIVERNY
git_commit: b5575b4
branch: master
topic: "Manual compaction trigger + visual verification of compaction events"
status: complete
---

# Research: Compaction — Manual Trigger & Visual Verification

## Summary

Two separate compaction systems exist: **Argus's own** (`context_manager.py`) which runs fully automatically via `agent_loop.py`, and **Claude Code's** which supports `/compact` manual trigger + `PreCompact`/`PostCompact` hooks. Neither system has a built-in visual indicator in the Argus GUI. The Argus GUI has the plumbing (SSE stream, command handler, agent card history) to show such events but nothing emits compaction signals to it today.

---

## File Locations

### Argus Compaction — Implementation
- `src/core/context_manager.py:30` — constants: `_MAX_CHARS_BEFORE_COMPACTION = 400_000`, `_TIER4_CHARS = 80_000`
- `src/core/context_manager.py:44` — `maybe_compact()` — tiered router (Tier 1 pass-through → Tier 4 hard-truncate+summarize)
- `src/core/context_manager.py:95` — `_do_compact()` — calls compaction LLM, logs token usage as `{agent_name}/_compaction`
- `src/core/context_manager.py:112` — compaction system prompt ("Extract key facts as compact bullet list")
- `src/core/context_manager.py:131` — token tracking label: `{agent_name}/_compaction`
- `src/core/context_manager.py:139` — `compact_injected_context()` — compacts Explorer/injected context
- `src/core/context_manager.py:175` — `trim_history()` — importance-scored sliding window
- `src/core/context_manager.py:263` — inserts `[Note: earlier context trimmed to fit context window]` marker into history

### Argus Compaction — Call Sites (all automatic, no manual trigger)
- `src/core/agent_loop.py:109-111` — `compact_injected_context()` before first message
- `src/core/agent_loop.py:262-263` — `maybe_compact()` per tool result
- `src/core/agent_loop.py:307-308` — `trim_history()` before each LLM API call

### Configuration
- `src/config.py:46` — `max_history_tokens: int = 50000`
- `src/config.py:47` — `compaction_threshold: int = 3000` (Tier 1/2 boundary)
- `src/config.py:48` — `compaction_model: str = "claude-haiku-4-5-20251001"`
- `src/config.py:49` — `max_context_injection_pct: float = 0.30`

### GUI — Command + Streaming Plumbing
- `src/gui/server.py:178` — `run_command` HTTP endpoint
- `src/gui/server.py:185` — `confirm_command` HTTP endpoint
- `src/gui/gui_app.py:108` — `handle_command()` — web command entry point
- `src/gui/static/prototype-a.html:143-155` — `.card-history` CSS — expandable per-agent activity panel
- `src/gui/static/prototype-a.html:355-470` — agent card history divs (static demo content)

### Tests
- `tests/test_argus.py:354-459` — `trim_history()` tests (multiple scenarios)

---

## How It Works

### Argus Compaction Flow
1. `agent_loop.py` calls `maybe_compact(tool_result)` after every tool execution
2. `maybe_compact()` measures char length → routes through tiers:
   - Tier 1 (< threshold): pass-through unchanged
   - Tier 2/3 (threshold–80k): LLM summarize via `compaction_model`
   - Tier 4 (> 80k): hard-truncate to 80k, then LLM summarize
3. `_do_compact()` fires Haiku with bullet-extraction prompt, logs as `{agent_name}/_compaction`
4. `trim_history()` runs before every API call, drops low-importance messages, inserts trim marker

**Nothing emits a UI event when compaction fires.**

### Claude Code Compaction
- `/compact` — manual slash command to force context compaction
- `PreCompact` hook — fires before compaction (matcher: `manual` | `auto`)
- `PostCompact` hook — fires after compaction
- No documented visual indicator in default UI — detectable via cost spike or programmatic hooks
- Compaction summary injected as structured user message with `isCompactSummary: true`

---

## Patterns Observed
- Token tracking at `context_manager.py:131` labels compaction events — could be surfaced
- Trim marker string at `context_manager.py:263` — text-only, not an event
- GUI SSE stream exists (server.py) but no compaction events flow through it
- Agent card history panel (`prototype-a.html:355`) is a natural display surface

## Open Questions
- Is DEV asking about Argus compaction or Claude Code `/compact`? (Likely Argus)
- Should manual trigger be: a GUI button, a CLI command, or both?
- Visual feedback target: agent card history? Toast/banner? Status bar?
- Should `trim_history()` events also surface visually, or just `maybe_compact()`?
