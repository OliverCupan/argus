---
date: 2026-06-05T00:00:00
researcher: GIVERNY
git_commit: a4228a022e08b298625ec313cb00234af7d38e4f
branch: master
topic: "What can we do to improve performance, efficiency or token usage?"
status: complete
---

# Research: Performance, Efficiency & Token Usage

## Summary
Argus has solid cost guardrails (caps, compaction, haiku for compaction) but leaves the single largest Anthropic API optimisation — **prompt caching** — completely unused. Secondary wins exist in serial tool execution within agent turns and serial file-reads before coder runs. The coding pipeline is otherwise well-structured; parallelism gaps are surgical, not architectural.

---

## File Locations

### LLM Call Points
- `src/core/llm_client.py:49` — `LLMClient.chat()` — single entry point for all LLM calls
- `src/core/llm_client.py:85` — `await self.client.messages.create(**params)` — actual API call
- `src/core/agent_loop.py:193` — ReAct loop LLM call — called every iteration; full message history resent
- `src/core/context_manager.py:119` — Compaction call (haiku) — one call per over-limit tool result
- `src/agents/orchestrator.py:158` — Intent classification call — separate call, max_tokens=256

### Token Tracking
- `src/core/token_tracker.py:59` — `TokenTracker.add()` — records usage per agent, computes cost
- `src/core/token_tracker.py:88` — `is_hard_cap_reached()` — hard cap check (tokens + dollars)
- `src/core/token_tracker.py:97` — `is_soft_cap_reached()` — soft cap check
- `src/core/token_tracker.py:110` — `is_agent_cap_reached()` — per-agent per-task cap
- `src/core/agent_loop.py:211` — `self.tracker.add(...)` — called after every LLM response

### Prompt Construction
- `src/agents/definitions.py:3–198` — All agent `system_prompt` strings (static, never change)
- `src/core/llm_client.py:73` — `"system": system` — system prompt passed as **plain string** (no `cache_control`)
- `src/core/agent_loop.py:110` — Injected context prepended to task before loop starts
- `src/core/context_manager.py:103` — Compaction system prompt — static string, no caching

### Model Routing
- `src/config.py:16` — `ModelConfig` — per-agent model slots
- `src/config.py:48` — `compaction_model` defaults to `claude-haiku-4-5-20251001`
- `src/core/agent_loop.py:83` — `get_model()` — resolves model once per `run()` call
- `src/agents/orchestrator.py:107` — `set_model()` — runtime model override

### Dispatch & Parallelism
- `src/tools/agent_dispatch.py:85` — `asyncio.gather(...)` — dispatched sub-agents run in parallel ✓
- `src/agents/orchestrator.py:314/318` — Auditors run in parallel via `asyncio.gather` ✓
- `src/core/agent_loop.py:239` — **Serial** `for tool_call in response.tool_calls` — NO gather ✗
- `src/agents/orchestrator.py:411-429` — **Serial** file reads before coder starts — NO gather ✗

### Retry / Redundant Work
- `src/agents/orchestrator.py:450-471` — HANDOFF: second full `coder.run()` spawned serially
- `src/agents/orchestrator.py:566-586` — CRITICAL audit auto-fix: third coder call (also serial)
- `src/core/llm_client.py:83` — Rate-limit retry: up to 4 retries, base delay 15 s, 4× backoff

### Context Management
- `src/core/context_manager.py:175-269` — `trim_history()` — token-budget based (not count-based)
- `src/core/agent_loop.py:300` — `trim_history()` called every iteration after tool results appended
- `src/core/agent_loop.py:259-264` — `maybe_compact(result)` — long tool outputs compacted before feedback

---

## How It Works

### ReAct Loop Token Growth
Each iteration appends 2 messages to `messages[]`: one assistant block (`agent_loop.py:235`) and one user block containing all tool results (`agent_loop.py:297`). `trim_history()` runs after every iteration and drops low-priority messages by token estimate until the list fits `max_history_tokens`. If the budget is not exceeded, no messages are dropped and the list grows unboundedly. The system prompt is re-sent as a **plain string** on every `messages.create()` call — it is never cached at the API level.

### No Prompt Caching
`llm_client.py:70-78` assembles `params` as `{model, max_tokens, system, messages, tools}`. There is no `cache_control` field anywhere in the codebase. Anthropic's prompt caching (available on claude-3.5+, claude-haiku-4-5+) would cache the system prompt and any static prefix for 5 min (ephemeral) at 10% of input cost. Currently every iteration pays full input price for the system prompt tokens.

### Serial Tool Execution
`agent_loop.py:239` iterates tool calls with a plain `for` loop. Each tool is awaited sequentially: `result = await self.tools.execute(...)` at line 248. If the model returns 3 independent `read_file` calls in one response, they execute one-by-one. This is a latency issue more than a token issue, but it slows each iteration.

### Serial Coder Context Injection
`orchestrator.py:411-429` reads path-hinted files in a `for hint in path_hints` loop before launching the Coder. These are independent file reads awaited serially.

### HANDOFF / CRITICAL Extra Coder Calls
When the Coder returns `"HANDOFF:"` (`orchestrator.py:450`), a second `coder.run()` is spawned. When an audit returns `"CRITICAL"` (`orchestrator.py:566`), a third coder call fires. Each is a full ReAct loop at full coder model pricing.

---

## Patterns Observed

| # | Pattern | Location | Impact |
|---|---------|----------|--------|
| 1 | No prompt caching | `llm_client.py:73` | **HIGH** — system prompts resent at full cost every iteration |
| 2 | Serial tool execution per turn | `agent_loop.py:239` | **MEDIUM** — latency per iteration, especially for read-heavy agents |
| 3 | Serial pre-coder file reads | `orchestrator.py:411-429` | **LOW-MEDIUM** — latency before coder starts |
| 4 | HANDOFF doubles coder spend | `orchestrator.py:450` | **MEDIUM** — doubles token cost when coder needs continuation |
| 5 | CRITICAL fires third coder pass | `orchestrator.py:566` | **LOW** — only on CRITICAL findings, but expensive when triggered |
| 6 | Intent classification LLM call | `orchestrator.py:158` | **LOW** — 256 max_tokens, cheap; could be regex/heuristic |
| 7 | No extended thinking / betas | `llm_client.py:70-78` | **NEUTRAL** — not necessarily needed, but worth knowing |

---

## Open Questions

- What is the actual numeric default for `max_history_tokens` in `config.py`? (Outside sandbox — need to check to estimate trim frequency.)
- Is the Anthropic account on a plan that supports prompt caching? (Prompt caching is available on all paid plans for claude-3.5+/haiku-4+.)
- What is the typical number of tool calls returned per LLM response in a real Explorer or Coder run? (Determines real-world gain from parallel tool execution.)
- Is the HANDOFF pattern necessary for correctness, or is it a workaround for context limits? (If the latter, prompt caching + larger context may eliminate it.)
