---
date: 2026-06-04T00:00:00
planner: GIVERNY
research_doc: thoughts/shared/research/2026-06-04-agent-workflow-efficiency.md
status: approved
iteration: 2
---

# Plan: Token Efficiency & Architecture Restructure

## Objective
Reduce total token spend per task, improve output quality per dollar, and eliminate dead maintenance surface — without breaking parallel audit execution or tool isolation.

## Research Reference
Based on: `thoughts/shared/research/2026-06-04-agent-workflow-efficiency.md`

---

## Phase 1: Model Specialization (Config-only)

**Problem:** Every agent uses `claude-haiku-4-5-20251001`. The Coder (most critical, writes code) gets the same model as auditors (pattern-match text). Haiku produces weaker code than Sonnet in the same iteration count — meaning Coder runs more iterations to get it right, spending *more* tokens at a lower quality ceiling.

**Change:** Assign models by role:
- `coder` → `claude-sonnet-4-6` (highest quality needed; fewer iterations compensate for higher per-token cost)
- `challenger` → `claude-sonnet-4-6` (plan quality directly determines Coder quality; cheap challenger = expensive coder rework)
- `explorer` → `claude-haiku-4-5-20251001` (just reads files, no reasoning needed)
- All 4 auditors → `claude-haiku-4-5-20251001` (pattern-matching, not reasoning; parallel so wall-clock same)
- `compaction_model` → `claude-haiku-4-5-20251001` (keep cheap)

### Sandbox
Files to modify:
- `argus.yaml` (lines 4–11: models block only)

Files to read (reference):
- `src/config.py` (verify model field names match config dataclass)

### Success Criteria
- [ ] `argus.yaml` models block: coder + challenger = sonnet-4-6, all others = haiku
- [ ] `argus run <task>` starts without config validation errors
- [ ] `argus audit <path>` starts without config validation errors
- [ ] Token tracker report shows different model names per agent after a run

### Verification
Manual (DEV must verify):
- [ ] Run a small coding task and check that the status line shows Sonnet for Coder
- [ ] Confirm cost doesn't blow through dollar_hard_cap on a trivial task

---

## Phase 2: Structured Explorer Output (Token Compression)

**Problem:** Explorer produces freeform prose. This prose is forwarded verbatim to:
- Challenger (1 copy)
- All 4 auditors (4 copies of the same string)

The Explorer summary is the single highest-multiplied payload in the system. Reducing its size directly reduces tokens for every downstream agent.

**Change:** Constrain Explorer's output to a structured compact format (Markdown with fixed sections). Add an explicit token limit instruction. The Orchestrator trims the injected context before forwarding if it exceeds a new `max_explorer_output_chars` config threshold.

Two sub-changes:
1. **Explorer system prompt** — Add explicit output structure + hard length limit ("your summary MUST fit in 800 tokens")
2. **Orchestrator._run_audit** — After receiving `explorer_result.content`, apply `context_manager.compact_injected_context()` before building `audit_task_base`. This is already implemented; it's just not called here.

### Sandbox
Files to modify:
- `src/agents/explorer.py` (system_prompt only — add structured format + length constraint)
- `src/agents/orchestrator.py` (lines 222–227: `audit_task_base` construction — apply compaction before building)

Files to read (reference):
- `src/core/context_manager.py` (verify `compact_injected_context()` signature)
- `src/core/agent_loop.py` (verify how context is passed to agents)

### Success Criteria
- [ ] Explorer system prompt explicitly specifies output sections and token limit
- [ ] `audit_task_base` in `_run_audit()` applies compaction to `explorer_context` if it exceeds 4000 chars before passing to auditors
- [ ] Audit run produces correct FINDING: blocks (output format unchanged)
- [ ] Explorer output length measurably shorter in a test run on the demo app

### Verification
Automated:
- [ ] `pytest tests/test_argus.py -k explorer` passes
Manual:
- [ ] Run `argus audit demo/buggy_app/` and verify report still has findings

---

## Phase 3: Config-Driven Agent Factory (Kill 8 Wrapper Classes)

**Problem:** 8 agent files (explorer.py, coder.py, challenger.py, bugs.py, performance.py, security.py, tests.py) each contain 30–45 lines that set: `name`, `system_prompt`, `get_model()`, `get_max_tokens()`, `get_tool_names()`. Zero custom logic. This is a config dict dressed as a class hierarchy — 8 files of maintenance surface with no behavioral return.

**Change:** Replace the 8 class files with:
1. An `AgentDefinition` dataclass in `src/core/agent_loop.py` (name, system_prompt, model_key, max_tokens, tool_names)
2. A `make_agent(defn: AgentDefinition, config, tracker, llm, tools) -> BaseAgent` factory
3. A single `src/agents/definitions.py` file containing all 8 definitions as dataclass instances
4. Orchestrator constructs agents via factory instead of direct class instantiation

The 8 old class files are deleted. `__init__.py` files cleaned up.

### Sandbox
Files to modify:
- `src/core/agent_loop.py` (add `AgentDefinition` dataclass + `make_agent` factory — append, don't rewrite `BaseAgent`)
- `src/agents/definitions.py` (NEW file — all 8 agent definitions as `AgentDefinition` instances)
- `src/agents/orchestrator.py` (replace direct class imports + instantiation with factory calls)
- `src/agents/__init__.py` (update exports)

Files to delete:
- `src/agents/explorer.py`
- `src/agents/coder.py`
- `src/agents/challenger.py`
- `src/agents/auditors/bugs.py`
- `src/agents/auditors/performance.py`
- `src/agents/auditors/security.py`
- `src/agents/auditors/tests.py`

Files to read (reference):
- `tests/test_argus.py` (understand what agent classes tests import — update imports in tests too)

### Success Criteria
- [ ] `src/agents/definitions.py` exists with all 8 `AgentDefinition` instances
- [ ] All 8 old agent class files deleted
- [ ] Orchestrator constructs all agents via `make_agent()`
- [ ] `pytest tests/test_argus.py` passes (may require updating test imports)
- [ ] `argus audit demo/buggy_app/` produces a report
- [ ] `argus` CLI starts without ImportError

### Verification
Automated:
- [ ] `pytest tests/test_argus.py` — full suite green
Manual:
- [ ] `argus audit demo/buggy_app/` runs end-to-end

---

## Phase 4: Structured Audit Findings (Kill Regex Parsing)

**Problem:** `Orchestrator._parse_findings()` (`orchestrator.py:780`) uses regex to extract `FINDING:`, `SEVERITY:`, `DESCRIPTION:`, `SUGGESTION:` blocks from freeform auditor output. Regex on LLM output is brittle — any deviation in formatting silently drops findings.

**Change:** Auditor system prompts are updated to output JSON findings arrays. The Orchestrator parses with `json.loads()` (with fallback to regex for backward compat). The `_format_audit_report()` consumes the structured list directly.

Auditor output format (each auditor returns):
```json
{"findings": [{"severity": "HIGH", "title": "...", "description": "...", "suggestion": "...", "file": "..."}]}
```

### Sandbox
Files to modify:
- `src/agents/definitions.py` (auditor system_prompts — add JSON output instruction, Phase 3 must complete first)
- `src/agents/orchestrator.py` (`_parse_findings()` at line 780 — replace regex with json.loads + fallback; `_format_audit_report()` — consume list directly)

Files to read (reference):
- `src/agents/orchestrator.py:780–820` (current regex implementation)

### Success Criteria
- [ ] All 4 auditor system prompts specify JSON output format with schema
- [ ] `_parse_findings()` attempts `json.loads()` first, falls back to regex if parse fails
- [ ] `_format_audit_report()` produces identical markdown output for equivalent findings
- [ ] `pytest tests/test_argus.py -k audit` passes
- [ ] Running `argus audit demo/buggy_app/` produces a correctly formatted report

### Verification
Automated:
- [ ] `pytest tests/test_argus.py` — full suite green
Manual:
- [ ] FINDING blocks appear in audit report with correct severity ordering
- [ ] Run with deliberately malformed auditor output (mock) to verify fallback fires

---

## Phase 5: Testing & Validation

Full regression pass after all phases complete.

### Sandbox
Files to read:
- `tests/test_argus.py`
- `demo/buggy_app/app.py`

### Success Criteria
- [ ] `pytest tests/test_argus.py` — full suite green
- [ ] `pytest tests/test_string_utils.py` — passes
- [ ] `argus audit demo/buggy_app/` — produces report with findings
- [ ] `argus` on a simple coding task — runs Explorer (Haiku) + Challenger (Sonnet) + Coder (Sonnet) + auditors (Haiku)
- [ ] Token tracker report shows correct model assignments per agent
- [ ] No regressions in CLI, GUI entry points

### Verification
Manual:
- [ ] Total token count for a coding task is measurably lower than pre-restructure baseline (if baseline was recorded)
- [ ] No ImportError from deleted agent class files

---

## Rollback Plan
If things break:
1. Phase 1: `git checkout argus.yaml` — zero risk
2. Phase 2: `git checkout src/agents/explorer.py src/agents/orchestrator.py`
3. Phase 3: `git checkout src/agents/` — restores deleted files if committed on separate branch
4. Phase 4: The regex fallback in `_parse_findings()` is the rollback — if JSON parse fails, behavior identical to today

**Recommendation:** Each phase is its own commit. If Phase 3 breaks tests, revert that commit without touching Phase 1/2.

---

## Open Questions
- [ ] **Q1 (DEV decision):** Is upgrading Coder + Challenger to `claude-sonnet-4-6` within budget? Sonnet is ~5x more expensive per token than Haiku, but fewer iterations may net out. Accept the cost increase?
- [ ] **Q2 (DEV decision):** Phase 3 deletes 8 files. Are any of these files referenced by the GUI, external scripts, or imports outside `src/agents/`? (Tests import them — handled in plan. Anything else?)
- [ ] **Q3 (DEV decision):** Phase 4 changes auditor output format. If there are saved/cached audit outputs being parsed anywhere, those will break. Are there?
- [ ] **Q4 (scope):** Enable `use_worktrees: true` by default? The `WorktreeManager` exists and is tested. Out of scope for now unless DEV wants it.
