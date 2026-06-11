---
date: 2026-06-05T00:00:00
planner: GIVERNY
research_doc: thoughts/shared/research/2026-06-05-dynamic-orchestration-coder-subagents.md
status: draft
iteration: 2
---

# Plan: Dynamic Orchestration + Coder Sub-Agents

## Objective
Replace the hardcoded regex routing with an LLM that decides which agents to run,
and give the Coder a `dispatch_agents` tool to spawn parallel sub-agents mid-task.

## DEV Decisions
1. Orchestrator picks from fixed pool OR spawns a "default worker" agent
2. Sub-agents get tool access appropriate for their job (defined per-agent by best practice)
3. Coder's sub-agents run in parallel
4. Each spawned sub-agent gets its own budget slice (tracked independently)
5. `dispatch_agents` available to Coder only (other agents don't benefit; recursion risk)
6. Query mode = Explorer only (fast path for read/lookup tasks)
7. Max simultaneous workers = 5 (configurable via `argus.yaml: agent.max_dispatch_workers`)

## Research Reference
Based on: `thoughts/shared/research/2026-06-05-dynamic-orchestration-coder-subagents.md`

---

## Phase 1: Worker Agent Definition + Budget Config

**Problem:** "Default worker" agent doesn't exist yet. Need a general-purpose agent
definition the Orchestrator and Coder can spawn for any auxiliary task (file analysis,
targeted search, quick research). Also need a per-agent budget entry for it.

**Worker agent spec:**
- Name: `worker`
- Model key: `explorer` (Haiku — cheap, appropriate for helper tasks)
- Tools: `read_file`, `bash` (can read and run commands; cannot write files)
- Max tokens: 2048
- System prompt: general-purpose assistant scoped to the task it receives

### Sandbox
Files to modify:
- `src/agents/definitions.py` (add `WORKER_DEF` at end of file)
- `argus.yaml` (add `worker: 30000` under `token_budget.per_agent`)

### Success Criteria
- [ ] `WORKER_DEF` exists in `definitions.py` with name `"worker"`, model_key `"explorer"`, tools `["read_file", "bash"]`, max_tokens `2048`
- [ ] `argus.yaml` has `worker: 30000` under `per_agent`
- [ ] `python -c "from src.agents.definitions import WORKER_DEF; print(WORKER_DEF.name)"` prints `worker`

### Verification
Automated:
- [ ] `pytest tests/test_argus.py -m unit -x -q` passes

---

## Phase 2: `dispatch_agents` Tool

**Problem:** No mechanism for any agent to spawn sub-agents. Need a new tool whose
handler: (1) resolves agent names to definitions, (2) instantiates agents via `make_agent()`,
(3) runs them in parallel with `asyncio.gather()`, (4) returns combined results as a string.

**Tool signature the LLM sees:**
```
dispatch_agents(tasks: list of {agent: str, task: str})
  agent: name from fixed pool ("explorer","challenger","coder","security_auditor",
         "bug_auditor","performance_auditor","test_auditor") OR "worker"
  task:  plain-text task description for that agent
Returns: combined string of all agent results, labelled by agent name
```

**Agent → tools mapping (best practice, read-only sub-agents cannot write):**
| Agent name | Tools granted |
|---|---|
| worker | read_file, bash |
| explorer | read_file, bash |
| security_auditor | read_file, bash |
| bug_auditor | read_file, bash |
| performance_auditor | read_file |
| test_auditor | read_file, bash |
| challenger | read_file |
| coder | read_file, edit_file, write_file, bash (full — use with care) |

**Budget:** Each sub-agent is tracked under its own `.name` in the TokenTracker,
so it uses its own per-agent cap from `argus.yaml`.

### Sandbox
Files to create:
- `src/tools/agent_dispatch.py` (new file)

Files to read (reference):
- `src/tools/bash_tool.py` (closure pattern to copy)
- `src/core/agent_loop.py` (make_agent signature)
- `src/agents/definitions.py` (all definition names)
- `src/tools/registry.py` (Tool dataclass structure)

### Success Criteria
- [ ] `src/tools/agent_dispatch.py` exists with a `create_agent_dispatch_tool(config, llm, tracker, tools, event_bus)` factory
- [ ] Handler resolves agent name → `AgentDefinition` from a local dict mapping all 8 names
- [ ] Unknown agent name returns an error string (does not raise)
- [ ] All resolved agents run via `asyncio.gather()` (parallel)
- [ ] Return value is a single string: each agent's result prefixed with `[agent_name]:`
- [ ] `coder` is NOT in the allowed dispatch list (prevent recursive full-write agents)
- [ ] Tool input schema lists `tasks` as an array of objects with `agent` (string) and `task` (string) required fields

### Verification
Automated:
- [ ] `python -c "from src.tools.agent_dispatch import create_agent_dispatch_tool; print('OK')"` passes

---

## Phase 3: Register Tool + Add to Coder

**Problem:** The new tool needs to be registered in `build_registry()` and added to
the Coder's allowed tool list. The Orchestrator also needs access to it for dynamic
pipeline execution (Phase 4). The tool factory needs `(config, llm, tracker, tools, event_bus)`
— `llm`, `tracker`, and `event_bus` are not currently passed to `build_registry()`.

**Change A — `build_registry()` signature:**
Add `llm=None`, `tracker=None`, `event_bus=None` as optional keyword arguments.
Register `dispatch_agents` only when `llm` and `tracker` are provided.

**Change B — Orchestrator passes resources to build_registry:**
At `orchestrator.py:65`, change:
`self.tools = build_registry(config, confirm_callback=confirm_callback)`
to:
`self.tools = build_registry(config, confirm_callback=confirm_callback, llm=self.llm, tracker=self.tracker, event_bus=event_bus)`

**Change C — Add tool to Coder:**
In `definitions.py`, add `"dispatch_agents"` to `CODER_DEF.tool_names`.

### Sandbox
Files to modify:
- `src/tools/registry.py` (`build_registry` signature + registration logic)
- `src/agents/orchestrator.py` (line ~65: `build_registry` call)
- `src/agents/definitions.py` (`CODER_DEF.tool_names`)

Files to read (reference):
- `src/tools/agent_dispatch.py` (verify function name)

### Success Criteria
- [ ] `build_registry(config)` still works with no extra args (backward compat — no llm/tracker = no dispatch tool registered)
- [ ] `build_registry(config, llm=llm, tracker=tracker)` registers `dispatch_agents`
- [ ] `CODER_DEF.tool_names` includes `"dispatch_agents"`
- [ ] Orchestrator passes `llm`, `tracker`, `event_bus` to `build_registry`
- [ ] `pytest tests/test_argus.py -m unit -x -q` passes

### Verification
Automated:
- [ ] `python -c "from src.agents.orchestrator import Orchestrator; print('OK')"` passes

---

## Phase 4: Dynamic Orchestrator Routing

**Problem:** Routing is a single regex. Replace with an LLM call that classifies intent
and returns a structured routing decision. Keep existing `_run_audit()` and
`_run_coding_task()` intact — the LLM chooses which to call (or runs a lightweight
custom path for simple queries).

**New routing call** — `_classify_intent(task: str) -> dict`:
- Uses `self.llm.chat()` with Haiku (fast, cheap)
- System prompt: "You are a task router. Given a user request, output JSON with:
  `mode` (one of: `audit`, `code`, `query`) and `params` (mode-specific data)."
- Returns: `{"mode": "audit", "target": "src/"}` or `{"mode": "code"}` or `{"mode": "query"}`
- `query` mode: simple tasks that don't need the full coding pipeline (e.g. "what files are in src/") — runs Explorer only
- Uses a JSON tool call for reliable structured output (not freeform text parsing)
- Falls back to `"code"` mode on any parse failure (safe default)

**Updated `handle()` method:**
```
1. Call _classify_intent(task) → routing_decision
2. Route:
   - "audit" → _run_audit(routing_decision["target"])
   - "code"  → _run_coding_task(task)  [existing, unchanged]
   - "query" → Explorer only, return result directly
```

### Sandbox
Files to modify:
- `src/agents/orchestrator.py` (`handle()` method + new `_classify_intent()` method)

Files to read (reference):
- `src/core/llm_client.py` (verify `chat()` signature and tool call format)
- `src/config.py` (verify `models.orchestrator` field exists for routing model)

### Success Criteria
- [ ] `handle()` calls `_classify_intent()` instead of the regex
- [ ] `_classify_intent()` uses `self.llm.chat()` with a JSON tool for structured output
- [ ] Parse failure falls back to `"code"` mode (no exception propagates)
- [ ] `audit src/` still routes to `_run_audit("src/")`
- [ ] `add rate limiting to /search` still routes to `_run_coding_task()`
- [ ] `query` mode runs Explorer only and returns its output directly
- [ ] `_run_audit()` and `_run_coding_task()` are NOT modified

### Verification
Automated:
- [ ] `pytest tests/test_argus.py -m unit -x -q` passes
Manual:
- [ ] `python main.py` → type `audit demo/buggy_app` → still produces findings report
- [ ] `python main.py` → type `what files are in src/` → routes to query mode (Explorer only, no Challenger/Coder)

---

## Phase 5: Validation

### Sandbox
Files to read:
- All modified files above

### Success Criteria
- [ ] `pytest tests/test_argus.py -m unit -x -q` — all pass
- [ ] `python -c "from src.agents.orchestrator import Orchestrator; print('OK')"` — passes
- [ ] `CODER_DEF.tool_names` contains `dispatch_agents`
- [ ] `WORKER_DEF` importable from `definitions.py`
- [ ] `argus.yaml` has `worker:` under `per_agent`

---

## Rollback Plan
1. Phase 1: `git checkout src/agents/definitions.py argus.yaml`
2. Phase 2: `rm src/tools/agent_dispatch.py`
3. Phase 3: `git checkout src/tools/registry.py src/agents/orchestrator.py src/agents/definitions.py`
4. Phase 4: `git checkout src/agents/orchestrator.py`

Each phase is independently revertible. Phase 4 (routing) can be reverted without affecting Phases 1-3 (sub-agents).

---

## Phase 6: Token Cap Handover / Continuation

**Problem:** When an agent hits its token cap mid-task it stops cold, returning a
bracket error or partial result. The Orchestrator ignores the early-stop signal —
it uses whatever string came back with no recovery. Best practice (LangGraph,
AutoGen) is: detect early-stop → emit a structured handoff → spawn a continuation
run seeded with the partial work.

**Two-part change:**

**Part A — Structured handoff note** (`agent_loop.py`):
When the soft-cap wind-down triggers (`_winding_down = True`), the agent's prompt
already says "wrap up and return findings." Add a second instruction: before stopping,
emit a structured handoff block at the end of the final response:

```
HANDOFF:
completed: <what was finished>
remaining: <what was not started or left incomplete>
context_for_next: <minimal state a fresh agent needs to continue>
```

This is added to the wind-down notice text injected at `agent_loop.py:282-288`.

**Part B — Continuation detection** (`orchestrator.py`):
In `_run_coding_task()`, after `coder_result = await self.coder.run(...)`:
- Check if `coder_result.content` contains `"HANDOFF:"` AND the early-stop note
- If yes: extract the `context_for_next` block, spawn a fresh Coder run with
  `context = original_context + "\n\nPrevious run handoff:\n" + handoff_block`
- Limit: max 1 continuation (prevents infinite retry loop)
- If continuation also hits cap: return combined partial results, no further retries

Same detection applied to Explorer result in `_run_audit()` — if Explorer
hits cap mid-mapping, use whatever summary it produced (already handled by
`explorer_result.content.startswith("[")` fallback at `orchestrator.py:194`).

### Sandbox
Files to modify:
- `src/core/agent_loop.py` (wind-down notice text at line ~282)
- `src/agents/orchestrator.py` (`_run_coding_task()` after coder_result line ~371)

Files to read (reference):
- `src/core/agent_loop.py:277-290` (current wind-down injection)
- `src/agents/orchestrator.py:371-380` (coder_result handling)

### Success Criteria
- [ ] Wind-down notice in `agent_loop.py` includes `HANDOFF:` section instruction
- [ ] `_run_coding_task()` checks for `HANDOFF:` in coder result
- [ ] If detected: one continuation Coder run is spawned with handoff context injected
- [ ] If continuation also stops early: combined result returned, no further retries
- [ ] Normal runs (no handoff) are completely unaffected
- [ ] `pytest tests/test_argus.py -m unit -x -q` passes

### Verification
Manual:
- [ ] Set `coder: 5000` tokens in argus.yaml (tiny cap), run a coding task, confirm
  handoff note appears in output and a continuation run starts

---

## Phase 7: Validation

### Sandbox
Files to read:
- All modified files above

### Success Criteria
- [ ] `pytest tests/test_argus.py -m unit -x -q` — all pass
- [ ] `python -c "from src.agents.orchestrator import Orchestrator; print('OK')"` passes
- [ ] `python -c "from src.tools.agent_dispatch import create_agent_dispatch_tool; print('OK')"` passes
- [ ] `CODER_DEF.tool_names` contains `"dispatch_agents"`
- [ ] `WORKER_DEF` importable from `definitions.py`
- [ ] `argus.yaml` has `worker: 30000` under `per_agent` and `max_dispatch_workers: 5` under `agent:`

---

## Rollback Plan
1. Phase 1: `git checkout src/agents/definitions.py argus.yaml`
2. Phase 2: `rm src/tools/agent_dispatch.py`
3. Phase 3: `git checkout src/tools/registry.py src/agents/orchestrator.py src/agents/definitions.py`
4. Phase 4: `git checkout src/agents/orchestrator.py`
5. Phase 6: `git checkout src/core/agent_loop.py src/agents/orchestrator.py`

All phases independently revertible. Phase 4 (routing) can be reverted without
affecting Phases 1-3 (sub-agents) or Phase 6 (handover).
