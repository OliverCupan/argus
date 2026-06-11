---
date: 2026-06-04T00:00:00
researcher: GIVERNY
git_commit: 670ccc1
branch: master
topic: "Are agents used in a way that makes development/workflow more efficient, or is it a waste of tokens?"
status: complete
---

# Research: Agent Workflow Efficiency Audit

## Summary

Argus is a multi-agent code review system (Explorer → Challenger → Coder → 4 Auditors) built on a shared `BaseAgent` class. Two concrete architectural benefits exist: **parallel auditor execution** (genuine 4x wall-clock speedup) and **enforced tool isolation** (write tools only for Coder). Everything else — the inheritance hierarchy, per-agent class files, model specialization infrastructure — is unused or cosmetic. Total token usage is significantly *higher* than a single-agent approach because Explorer's codebase context is re-tokenized every time it flows downstream as injected strings.

---

## File Locations

### Implementation
- `src/core/agent_loop.py:83` — `BaseAgent.run()` — ALL execution logic lives here; every agent is identical at runtime
- `src/agents/orchestrator.py:239` — `asyncio.gather()` for parallel auditors (real concurrency)
- `src/agents/orchestrator.py:283–501` — coding pipeline: Explorer → Challenger → Coder → audit (sequential)
- `src/agents/explorer.py:11` — thin wrapper, ~37 lines, only overrides name/prompt/model/tools
- `src/agents/coder.py:11` — thin wrapper, ~40 lines
- `src/agents/challenger.py:12` — thin wrapper, ~41 lines; only tool is `read_file`
- `src/agents/auditors/bugs.py:11` — thin wrapper, ~45 lines
- `src/agents/auditors/performance.py:11` — thin wrapper, ~44 lines
- `src/agents/auditors/security.py:11` — thin wrapper, ~44 lines
- `src/agents/auditors/tests.py:11` — thin wrapper, ~40 lines
- `src/core/context_manager.py:150` — `compact_injected_context()`, caps injected context at 30% of history budget
- `src/core/token_tracker.py:110` — per-agent cap enforcement

### Configuration
- `argus.yaml:3–11` — **every single agent configured to `claude-haiku-4-5-20251001`** — model specialization infrastructure exists but is unused
- `argus.yaml:68` — `parallel_audit: true` (default on)
- `argus.yaml:69` — `use_worktrees: false` (worktree isolation exists but disabled)
- `argus.yaml:63–66` — per-agent iteration caps (coder: 30, explorer: 12, challenger: 8)
- `src/config.py:16` — `AgentConfig`, `TokenBudget`, `ContextConfig` dataclasses

### Tests
- `tests/test_argus.py` — 1195 lines, covers all agents and core modules

---

## How It Works

### Data Flow

**Audit mode:**
```
Explorer.run() 
  → codebase summary string
  → injected into all 4 auditors simultaneously (asyncio.gather)
  → each auditor produces FINDING: blocks
  → Orchestrator._format_audit_report() parses via regex → markdown
```

**Coding mode:**
```
Explorer.run()
  → .content string (codebase map)
  → injected into Challenger.run()
    → .content string (critique/plan)
    → concatenated with raw file contents (up to 30k chars)
    → injected into Coder.run()
      → .content string (implementation summary)
      → Orchestrator._build_delta_context() = git diff + new file content
      → auditors run in parallel on delta context
      → if CRITICAL findings: Coder.run() again to fix
```

Data passing is **pure string concatenation** throughout. No structured objects cross agent boundaries — just `.content` strings injected as context prefixes.

### What BaseAgent.run() Actually Does (agent_loop.py:83)

Every agent, regardless of class, runs this identical loop:
1. Build messages array (system prompt + injected context + task)
2. Call LLM API via `LLMClient`
3. If tool calls in response: execute tools, append results
4. Repeat until no tool calls or iteration cap reached
5. Return `AgentResult(content=last_message)`

---

## Patterns Observed

### Genuine Architectural Benefits
1. **Parallel auditors** (`orchestrator.py:239`) — `asyncio.gather()` runs 4 auditors concurrently. Real ~4x wall-clock reduction for audit phase.
2. **Tool isolation** — Challenger: `["read_file"]` only. Auditors: `["read_file", "bash"]`. Coder: full write toolset. Enforced at registry level, not model trust.
3. **Per-agent iteration caps** (`argus.yaml:63–66`) — prevents cheap agents from burning full budget.
4. **Clean context per agent** — each agent starts with fresh `ContextManager`; auditors don't inherit Coder's multi-turn noise.

### Architectural Waste / Cosmetic Structure
1. **All same model** (`argus.yaml:3–11`) — the infrastructure to use different models per agent exists and is zero-cost to use, but every agent is `claude-haiku-4-5-20251001`. No specialization at runtime.
2. **Token multiplication** — Explorer's output is tokenized at minimum 3 times in coding mode: once during Explorer's run, once injected into Challenger, once injected into Coder. The 4 auditors each separately receive the same `audit_task_base` containing full Explorer output — identical content tokenized 4× in parallel.
3. **8 class files, ~0 custom logic** — every agent class is 30-45 lines that set 3-4 properties. All logic is in `BaseAgent`. The class hierarchy adds file maintenance overhead with zero behavioral return beyond what a config dict would provide.
4. **Worktrees disabled** (`argus.yaml:69: use_worktrees: false`) — the `WorktreeManager` (211 lines) and its git isolation feature exist but is off by default.
5. **String-only inter-agent protocol** — no structured data (JSON, typed objects) crosses agent boundaries. Output parsing relies on regex (`_parse_findings` at `orchestrator.py:780`), which is brittle.

---

## Verdict: Honest Assessment

**The multi-agent architecture is ~30% genuinely useful, ~70% ceremony.**

The two real wins (parallel auditors + tool access control) justify having *some* multi-agent structure. Without parallelism, audit wall-clock time would be 4x longer. Without tool isolation, the Challenger could accidentally write files.

The rest is overhead: 8 nearly-identical class files, infrastructure for model specialization that isn't used, context duplication that multiplies token spend significantly, a worktree feature that's disabled. The total token cost of a coding task is materially higher than a single-agent equivalent because the same codebase context flows through 3+ agent boundaries as repeated injected strings.

**What a single-agent replacement would lose:** parallel auditors (wall-clock), enforced tool gating per phase, per-phase token caps.

**What it would gain:** substantially lower total token spend, simpler codebase, no cross-agent context duplication.

**Most impactful improvement available without redesign:** use different models per phase. Explorer and Challenger are currently using the same Haiku model as the Coder, despite the infrastructure supporting Sonnet/Opus for Coder and Haiku for the rest. This is a config-only change that would improve output quality at the same or lower cost.

---

## Open Questions

1. Why are all agents the same model? Cost cap? Oversight? Is this intentional permanent design or temporary?
2. Why is `use_worktrees` disabled by default — stability issues, or just not needed yet?
3. The regex-based `_parse_findings` at `orchestrator.py:780` — how robust is it? Does it fail silently on malformed agent output?
4. Token multiplication is significant — has total per-task token cost been measured and compared to a baseline single-agent approach?
