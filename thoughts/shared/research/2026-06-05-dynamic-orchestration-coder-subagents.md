---
date: 2026-06-05T00:00:00
researcher: GIVERNY
git_commit: 670ccc1
branch: master
topic: "Dynamic orchestrator routing + Coder spawning its own sub-agents"
status: complete
---

# Research: Dynamic Orchestration & Coder Sub-Agents

## Summary

Both features are buildable without redesigning the core. The infrastructure
is already more than halfway there: `make_agent()` can instantiate any agent
at runtime, the auditor filter is already name-based, and the tool handler
closure pattern is established. What is missing is (1) an LLM routing call
that replaces the regex gate, and (2) a `dispatch_agent` tool that closes
over the orchestrator's shared resources and lets the Coder spin up agents
as tool calls.

---

## File Locations

### Current Routing (Hardcoded)
- `src/agents/orchestrator.py:128` — `re.match(r"^audit\b"...)` — the entire routing decision; regex only, no LLM involved
- `src/agents/orchestrator.py:132` — `await self._run_audit(target_path)` — audit branch
- `src/agents/orchestrator.py:136` — `await self._run_coding_task(stripped)` — coding branch

### Agent Instantiation
- `src/agents/orchestrator.py:85` — `_mk = lambda defn: make_agent(...)` — factory lambda
- `src/agents/orchestrator.py:86–94` — all 7 agents instantiated at `__init__` time (fixed)
- `src/core/agent_loop.py:305–318` — `make_agent(defn, config, llm, tracker, tools)` — fully reusable factory

### Agent Definitions (plain data, not class hierarchy)
- `src/agents/definitions.py:3` — `EXPLORER_DEF`
- `src/agents/definitions.py:33` — `CHALLENGER_DEF`
- `src/agents/definitions.py:58` — `CODER_DEF`
- `src/agents/definitions.py:83–171` — 4 auditor defs

### Existing Partial Dynamic Selection
- `src/agents/orchestrator.py:152` — `_run_audit(auditor_filter: Optional[list[str]])` — already supports name-based filtering
- `src/agents/orchestrator.py:206–213` — `active_auditors = [a for a in self.auditors if a.name in auditor_filter]` — filter application
- `src/agents/orchestrator.py:585–602` — `_select_auditor_names(touched_files)` — heuristic selection (Security+Bug always; Perf/Tests conditionally)
- `src/agents/orchestrator.py:419` — `auditor_filter = self._select_auditor_names(coder_touched)` — called from coding pipeline

### Tool Registry Pattern
- `src/tools/registry.py:30` — `class ToolRegistry` — dict of `Tool` objects, dispatches by name
- `src/tools/registry.py:62` — `async def execute(name, inputs) -> str` — returns string only
- `src/tools/registry.py:96–117` — `build_registry(config, confirm_callback)` — factory; all tools close over `config`
- `src/core/agent_loop.py:120` — `tool_schemas = self.tools.get_schemas(self.get_tool_names())` — per-agent tool restriction
- `src/core/agent_loop.py:248` — `result = await self.tools.execute(...)` — every tool call dispatches here

### Shared Resources (safe to share)
- `src/agents/orchestrator.py:64–65` — `self.llm = LLMClient(config)`, `self.tracker = token_tracker` — single instances, shared by all agents
- `src/core/agent_loop.py:174–189` — hard/soft cap checks — only safety boundary against runaway sub-agents
- `src/core/file_lock.py` — `FileLockManager` with `write_lock()` — concurrent file write protection already exists

---

## How It Works Now

**Routing:** A single regex at `orchestrator.py:128`. If input starts with "audit" → `_run_audit()`. Anything else → `_run_coding_task()`. No LLM is consulted for this decision.

**Pipeline:** Both methods are hardcoded sequences. The coding pipeline always runs Explorer → Challenger → Coder → auto-audit. The audit pipeline always runs Explorer → 4 auditors. There is no graph, no plan object, no runtime reconfiguration.

**Agent creation:** All agents are pre-instantiated at `__init__` time. The `make_agent()` factory exists and is reusable, but is only called once per agent during construction.

**Tool dispatch:** Every tool call returns a `str`. There is no mechanism for a tool to spawn another agent or return structured data. The handler pattern is a simple async closure.

---

## Patterns Observed

### What Already Supports Dynamic Orchestration
1. `make_agent(defn, ...)` at `agent_loop.py:305` — instantiates any agent from a `AgentDefinition` data object at runtime. No subclassing needed.
2. `auditor_filter` parameter on `_run_audit()` — name-based selection already wired in.
3. `self.llm` is stateless per-call — safe to pass to dynamically created agents.
4. `AgentDefinition` is a plain dataclass — an LLM could output a JSON spec that maps directly to it.

### What Is Missing
1. No LLM routing call — would need a new `_classify_intent()` method on the Orchestrator that calls `self.llm.chat()` with a routing prompt and parses structured output.
2. No plan/graph data structure — execution order is implicit Python call order; would need an `ExecutionPlan` type (ordered list of steps with agent name + context rules).
3. No `dispatch_agent` tool — would need a factory function that closes over `(config, llm, tracker, tools)` and returns a `Tool` whose handler calls `make_agent()` + `agent.run()`.
4. No sub-agent result serialization — `ToolRegistry.execute()` returns `str`; sub-agent `AgentResult` would need to be serialized to string for the calling agent to consume.
5. No circular-spawn guard — nothing prevents a sub-agent from calling `dispatch_agent` again. Token tracker caps are the only limit.

---

## Open Questions

- **Scope of dynamic routing:** Should the LLM choose from the existing fixed agent pool, or can it define new agents with custom prompts on the fly?
- **Coder sub-agent tool access:** When Coder spawns a sub-agent, should that sub-agent have the same tool access as Coder (full write) or a restricted set?
- **Parallel vs serial sub-agents:** Should the Coder be able to spawn multiple sub-agents and await them in parallel (like the auditor pool), or always serial?
- **Budget allocation:** Should each spawned sub-agent get a slice of the parent's per-agent cap, or share the global pool?
- **Depth limit:** Maximum nesting depth for sub-agent spawning (currently unlimited — only token cap stops it).
