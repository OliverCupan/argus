# Argus — Build Plan

## What Argus Is

A terminal-based multi-agent coding assistant with a unique self-auditing capability.
It can handle normal coding tasks (read files, edit code, run bash) AND automatically
audit its own output through parallel specialist agents before presenting results.

For explicit `audit` commands, it scans an entire codebase with parallel agents
(Security, Bugs, Performance, Tests) and can auto-fix findings.

## Architecture Overview

```
User Input
    │
    ▼
┌──────────────┐
│ Orchestrator  │  (Sonnet) — decides mode, delegates, synthesizes
└──────┬───────┘
       │
       ├── Normal coding task ──────────────────────────┐
       │                                                 │
       │   ┌──────────┐  ┌────────────┐  ┌───────────┐  │
       │   │ Explorer  │  │ Challenger │  │   Coder   │  │
       │   │ (Haiku)   │  │ (Sonnet)   │  │ (Sonnet)  │  │
       │   └──────────┘  └────────────┘  └───────────┘  │
       │                                                 │
       │   After coding ─► Auto-Audit Pipeline ──────────┘
       │
       ├── Audit command ───────────────────────────────┐
       │                                                 │
       │   Parallel:                                     │
       │   ┌──────────┐ ┌──────┐ ┌───────┐ ┌─────────┐  │
       │   │ Security │ │ Bugs │ │ Perf  │ │  Tests  │  │
       │   │ (Sonnet) │ │(Son.)│ │(Haiku)│ │ (Haiku) │  │
       │   └──────────┘ └──────┘ └───────┘ └─────────┘  │
       │                                                 │
       │   Orchestrator ranks findings, offers auto-fix  │
       └─────────────────────────────────────────────────┘
```

## Component Map

### 1. Entry Point & CLI (`main.py`, `src/cli.py`)
- Rich-based terminal UI
- Input loop with command parsing
- Displays agent status, findings, token costs
- Commands: free-form prompts, `audit <path>`, `fix <finding_id>`, `exit`

### 2. Config (`src/config.py`, `argus.yaml`, `.env`)
- YAML config: model routing, token budgets, safety rules
- .env: API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY)
- Validates config on startup

### 3. Core Infrastructure (`src/core/`)

#### `llm_client.py` — LLM API Wrapper
- Supports Anthropic Messages API (primary)
- Optional: OpenAI-compatible endpoint
- Sends messages, receives responses, extracts tool calls
- Returns token counts with every response

#### `agent_loop.py` — ReAct Agent Loop
- Base class all agents inherit from
- Loop: Think → Act (tool call) → Observe (tool result) → repeat or yield
- Agent decides: more tool calls OR return final answer
- Receives: system prompt, tools list, conversation history
- Configurable max iterations (prevent infinite loops)

#### `token_tracker.py` — Token Cost Monitor
- Tracks input/output tokens per agent
- Calculates USD cost based on model pricing
- Fires warning at configurable threshold (default 80%)
- Hard cap kills the agent run
- Exposes live stats for the TUI

#### `context_manager.py` — Context Engineering
- Compacts long tool outputs (bash results, file contents)
- Strategy: if tool output > threshold tokens, summarize via Haiku
- Sliding window: drops oldest messages when context approaches limit
- Per-agent context isolation (each agent has its own history)

#### `safety.py` — Tool Call Safety
- Classifies bash commands: SAFE / REVIEW / BLOCKED
- BLOCKED: rm -rf, sudo, mkfs, etc. → rejected automatically
- REVIEW: rm, pip install, git push → requires user confirmation
- SAFE: ls, cat, grep, python, pytest → runs immediately
- File write protection: blocks writes outside project directory

### 4. Tools (`src/tools/`)

#### `registry.py` — Tool Registry
- Central registry mapping tool names to implementations
- Generates tool schemas for LLM API calls
- Dispatches tool calls to correct handler

#### `bash_tool.py` — Bash Execution
- Runs shell commands with safety check
- Captures stdout, stderr, return code
- Timeout protection (default 30s)

#### `file_reader.py` — Read Files
- Read single file content
- List directory tree
- Supports line range selection

#### `file_editor.py` — Partial File Editing
- Search/replace within files
- Requires unique match (prevents ambiguous edits)
- Returns diff of what changed

#### `file_writer.py` — Create New Files
- Write new files to disk
- Refuses to overwrite without explicit flag

### 5. Agents (`src/agents/`)

#### `orchestrator.py` — The Conductor
- Receives user input, classifies intent (code task vs audit vs question)
- For coding tasks: spawns Explorer → Challenger → Coder → Auto-Audit
- For audit tasks: spawns parallel auditors, synthesizes ranked report
- Decides when to yield back to user

#### `explorer.py` — Codebase Scout (Haiku)
- Maps project structure (file tree, key files)
- Reads relevant files based on task description
- Produces structured summary for other agents
- Keeps output compact (context engineering in action)

#### `challenger.py` — Plan Critic (Sonnet)
- Receives the proposed plan/approach
- Pokes holes: edge cases, scalability, security concerns
- Returns improved plan or approval
- Short-circuits if plan is simple enough

#### `coder.py` — Code Writer (Sonnet)
- Receives plan + relevant file contents
- Makes edits via file_editor tool (search/replace)
- Creates new files via file_writer
- Runs commands to verify (pytest, etc.)

#### `auditors/security.py` — Security Specialist (Sonnet)
- Scans for: injection, hardcoded secrets, auth gaps, input validation
- Returns structured findings with severity + location + suggestion

#### `auditors/bugs.py` — Bug Hunter (Sonnet)
- Scans for: logic errors, unhandled edge cases, type mismatches
- Returns structured findings

#### `auditors/performance.py` — Performance Analyst (Haiku)
- Scans for: O(n²) patterns, unnecessary allocations, N+1 queries
- Returns structured findings

#### `auditors/tests.py` — Test Runner (Haiku)
- Runs existing test suite
- Identifies failing tests
- Spots untested critical paths
- Returns structured findings

### 6. Demo Project (`demo/buggy_app/`)
- Small Flask/FastAPI app with planted bugs:
  - SQL injection in a query endpoint
  - Hardcoded API key in source
  - O(n²) loop in data processing
  - Unhandled None edge case
  - Failing test
- Used for demos and development testing

## Build Phases

### Phase 1: Foundation (Days 1-3)
**Goal:** Single agent that can chat, read files, edit files, run bash.

- [ ] `config.py` — load YAML + .env
- [ ] `llm_client.py` — Anthropic API wrapper with tool use
- [ ] `token_tracker.py` — basic counting and hard cap
- [ ] `safety.py` — bash command classifier
- [ ] All tools: bash, file_reader, file_editor, file_writer
- [ ] `tool_registry.py` — register tools, generate schemas
- [ ] `agent_loop.py` — base ReAct loop
- [ ] `main.py` — simple input loop (no Rich yet)
- [ ] Test: can it read a file, edit it, and run a command?

### Phase 2: Multi-Agent Audit (Days 4-7)
**Goal:** Parallel audit agents that scan code and report findings.

- [ ] `orchestrator.py` — detect audit vs code intent
- [ ] `explorer.py` — map codebase, feed relevant files to agents
- [ ] All 4 auditors with specialized system prompts
- [ ] Parallel execution (asyncio.gather or threading)
- [ ] Orchestrator synthesizes findings into ranked report
- [ ] `demo/buggy_app/` — plant 4-5 bugs
- [ ] Test: `audit demo/buggy_app` finds all planted bugs

### Phase 3: Self-Auditing Coding (Days 8-10)
**Goal:** Coding tasks auto-audit before presenting results.

- [ ] `challenger.py` — plan critic agent
- [ ] `coder.py` — code writing agent
- [ ] Orchestrator flow: Explorer → Challenger → Coder → Auto-Audit
- [ ] Auto-fix for audit findings (reuse coder agent)
- [ ] `context_manager.py` — compaction for long outputs
- [ ] Token budget warnings in TUI

### Phase 4: Polish & Package (Days 11-14)
**Goal:** Docker, Rich TUI, README, demo-ready.

- [ ] Rich TUI: agent status panels, token dashboard, colored findings
- [ ] Docker: Dockerfile + docker-compose.yml
- [ ] README with architecture diagram and usage
- [ ] Record demo GIF (asciinema)
- [ ] End-to-end test: full audit + auto-fix flow
- [ ] Edge cases: empty projects, no tests found, API errors

## Key Design Decisions

1. **Anthropic Messages API as primary** — tool use built-in, reliable
2. **asyncio for parallel agents** — simpler than threading for I/O-bound work
3. **Rich for TUI** — battle-tested, great live displays
4. **All config in YAML** — no hardcoded model names, budgets, or rules
5. **Tool outputs are the context engineering bottleneck** — compact aggressively
6. **Each agent is stateless** — gets context injected, returns result, done
