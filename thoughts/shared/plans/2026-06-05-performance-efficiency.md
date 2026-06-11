---
date: 2026-06-05T00:00:00
planner: GIVERNY
research_doc: thoughts/shared/research/2026-06-05-performance-efficiency-token-usage.md
status: approved
iteration: 1
---

# Plan: Performance, Efficiency & Token Usage

## Objective
Reduce per-session token cost and iteration latency by enabling Anthropic prompt caching on all LLM calls and parallelising independent I/O operations.

## Research Reference
Based on: `thoughts/shared/research/2026-06-05-performance-efficiency-token-usage.md`

---

## Phase 1: Prompt Caching (HIGH impact)

**What:** Enable Anthropic's prompt caching on the system prompt and tool definitions in every `messages.create()` call. This drops cached input tokens to 10% of list price.

**Why this works:** System prompts are static per agent (100–400 tokens each). Tool schemas are also static. On a 10-iteration agent run, both are resent 10× at full price. Adding `cache_control: {type: ephemeral}` wraps them in a 5-minute cache. All subsequent iterations within a run (and any run within 5 min) pay only 10% for those tokens.

Also tracks `cache_creation_input_tokens` and `cache_read_input_tokens` from the response for accurate cost display.

### Sandbox
Files to modify:
- `src/core/llm_client.py` — convert `system: str` param to cached content block in params; add `cache_control` to last tool in tools list; extract cache token counts from `response.usage`
- `src/core/token_tracker.py` — add `cache_creation_tokens` and `cache_read_tokens` fields to `add()` and accumulate them; update `get_summary()` to report them; update cost formula to use cache_read price (10% of input price)

Files to read (reference only):
- `src/config.py` — confirm `compaction_model` value (cache applies there too via context_manager)
- `src/core/context_manager.py` — confirm compaction LLM call flows through `llm_client.chat()` so it gets caching for free

### Success Criteria
- [ ] `params["system"]` is `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]` not a plain string
- [ ] When `tools` is non-empty, the last entry has `"cache_control": {"type": "ephemeral"}` appended
- [ ] `_parse_response()` reads `response.usage.cache_creation_input_tokens` and `response.usage.cache_read_input_tokens` (both default to 0 if absent)
- [ ] `LLMResponse` has two new int fields: `cache_creation_tokens`, `cache_read_tokens`
- [ ] `TokenTracker.add()` accepts and accumulates these two fields
- [ ] `get_summary()` includes `cache_creation_tokens` and `cache_read_tokens` totals
- [ ] Cost formula: cache_read tokens billed at `pricing["input"] * 0.1` instead of full input price
- [ ] Existing tests pass: `pytest tests/`

### Verification
Automated:
- [ ] `pytest tests/` passes

Manual (DEV must verify):
- [ ] Run a real Argus task; check that `response.usage` in debug logs shows non-zero `cache_read_input_tokens` from iteration 2 onward

---

## Phase 2: Parallel Tool Execution (MEDIUM — latency)

**What:** Within a single agent turn, execute all tool calls returned by the LLM concurrently instead of serially.

**Why this works:** When the model returns e.g. 4 `read_file` calls in one response, they currently wait on each other. Most tools are I/O-bound (file reads, bash). Running them with `asyncio.gather` reduces turn latency from `N × tool_time` to `max(tool_time)`.

### Sandbox
Files to modify:
- `src/core/agent_loop.py` — replace the serial `for tool_call in response.tool_calls` loop (lines 239–275) with a coroutine-per-tool gathered via `asyncio.gather`; each coroutine handles emit events + execute + compact for its own tool call; results assembled in original order for the tool_results list

### Success Criteria
- [ ] All tool calls from a single LLM response are dispatched to `asyncio.gather` concurrently
- [ ] `tool_result` blocks in the outgoing user message preserve the same ordering as `response.tool_calls` (required by Anthropic API — tool_result must reference the correct `tool_use_id`)
- [ ] Emit events (`tool_call`, `tool_result`) still fire per tool call (order may interleave, that is acceptable)
- [ ] Serial tool calls (if only one) incur no behavioural change
- [ ] Existing tests pass: `pytest tests/`

### Verification
Automated:
- [ ] `pytest tests/` passes

Manual (DEV must verify):
- [ ] In debug logs for an Explorer run with multiple file reads in one turn, confirm tool calls fire nearly simultaneously (timestamps close together)

---

## Phase 3: Parallel Coder Context File Reads (LOW-MEDIUM — latency)

**What:** Replace the serial `for hint in path_hints` file-read loop in `orchestrator.py` (lines 416–428) with concurrent reads using `asyncio.to_thread` or `asyncio.gather`.

**Why this works:** Each `hp.read_text(...)` is a synchronous blocking call. With 5–10 path hints, these stack serially before the Coder starts. Gathering them in a thread pool eliminates that wait.

Note: The 30k char cap logic must be preserved — gather all reads, then apply the cap in-order on results.

### Sandbox
Files to modify:
- `src/agents/orchestrator.py` — replace the serial `for hint in path_hints` loop (lines 416–428) with async-gathered reads; preserve the 30k cap by collecting all results then truncating in original hint order

### Success Criteria
- [ ] All file reads for `path_hints` are dispatched concurrently
- [ ] The 30k char injection cap (`_INJECT_MAX_CHARS`) is respected — total injected content does not exceed 30k chars
- [ ] File sections in the injected context appear in the same order as `path_hints` (Coder context is order-sensitive)
- [ ] Files that fail to read (`OSError`) are silently skipped, same as before
- [ ] Existing tests pass: `pytest tests/`

### Verification
Automated:
- [ ] `pytest tests/` passes

Manual (DEV must verify):
- [ ] Coder context block still contains the expected file sections in the right order on a multi-file task

---

## Phase 4: Testing & Validation

### Sandbox
Files to read (reference only):
- `tests/test_argus.py`
- `tests/test_string_utils.py`

### Success Criteria
- [ ] `pytest tests/ -v` passes with zero failures
- [ ] No regressions in token tracking output (totals still display correctly)
- [ ] Cache token fields show in `get_summary()` output (can be 0 if no real API call)

---

## Rollback Plan
1. All changes are in 3 files (`llm_client.py`, `agent_loop.py`, `orchestrator.py`) plus `token_tracker.py`
2. `git diff` shows only these 4 files touched
3. `git checkout src/core/llm_client.py src/core/agent_loop.py src/agents/orchestrator.py src/core/token_tracker.py` restores original state

---

## Open Questions (need DEV decision before /implement)

1. **Cache TTL scope:** Prompt caching caches for 5 min. Within a single multi-minute run this is fine. Do we need any config flag to disable caching (e.g. for testing against a mock API that doesn't support `cache_control`)? Or is silent pass-through acceptable if the API ignores unknown fields?

2. **Phase 2 event ordering:** `tool_call` and `tool_result` events from concurrent tools will interleave in the event bus. The GUI/CLI displays them as a stream. Is interleaved ordering acceptable, or should events be serialised after gather?

3. **Scope:** Phases 1–3 are in scope. Excluded (deferred): HANDOFF token optimisation, intent classification heuristic replacement. Confirm this scope is correct.
