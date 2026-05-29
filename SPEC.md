# Argus — Complete Build Specification

## Project Summary

Argus is a terminal-based multi-agent coding assistant built in Python. It has two modes: a coding mode where it reads, edits, and creates files with automatic self-auditing, and an audit mode where parallel specialist agents scan a codebase for security, bugs, performance, and test issues. The user interacts via a Rich-powered terminal UI.

## Tech Stack

- Python 3.12
- Anthropic Messages API (with tool use) — primary LLM provider
- asyncio — parallel agent execution
- Rich — terminal UI (panels, live displays, colored output)
- PyYAML — configuration
- python-dotenv — environment variable loading
- tiktoken — token counting (optional, can use len/4 heuristic)
- Docker + docker-compose — packaging

## Project Structure

```
argus/
├── main.py                          # Entry point
├── argus.yaml                       # All configuration
├── .env.example                     # API key template
├── .env                             # Actual API keys (gitignored)
├── requirements.txt                 # Python dependencies
├── Dockerfile
├── docker-compose.yml
├── README.md
├── src/
│   ├── cli.py                       # Rich terminal interface
│   ├── config.py                    # YAML + env loader with dataclasses
│   ├── core/
│   │   ├── llm_client.py            # Anthropic API wrapper
│   │   ├── agent_loop.py            # Base ReAct loop (all agents inherit)
│   │   ├── token_tracker.py         # Per-agent token/cost monitoring
│   │   ├── context_manager.py       # Tool output compaction + sliding window
│   │   └── safety.py                # Bash command classification
│   ├── tools/
│   │   ├── registry.py              # Tool registration + dispatch
│   │   ├── bash_tool.py             # Shell command execution
│   │   ├── file_reader.py           # Read files + list directories
│   │   ├── file_editor.py           # Partial file editing (search/replace)
│   │   └── file_writer.py           # Create new files
│   └── agents/
│       ├── orchestrator.py          # Main conductor — routes and synthesizes
│       ├── explorer.py              # Codebase scout (Haiku)
│       ├── challenger.py            # Plan critic (Sonnet)
│       ├── coder.py                 # Code writer (Sonnet)
│       └── auditors/
│           ├── security.py          # Security vulnerability scanner (Sonnet)
│           ├── bugs.py              # Logic error hunter (Sonnet)
│           ├── performance.py       # Performance issue finder (Haiku)
│           └── tests.py             # Test runner + coverage checker (Haiku)
└── demo/
    └── buggy_app/
        ├── app.py                   # Flask app with 5 planted bugs
        └── test_app.py              # Tests including 1 failing test
```

---

## Configuration

### argus.yaml

Controls all behavior. No hardcoded values in source code.

```yaml
models:
  orchestrator: claude-sonnet-4-20250514
  challenger: claude-sonnet-4-20250514
  coder: claude-sonnet-4-20250514
  explorer: claude-haiku-4-5-20251001
  security_auditor: claude-sonnet-4-20250514
  bug_auditor: claude-sonnet-4-20250514
  performance_auditor: claude-haiku-4-5-20251001
  test_auditor: claude-haiku-4-5-20251001

token_budget:
  total_hard_cap: 500000
  warning_threshold: 0.8
  per_agent:
    orchestrator: 100000
    challenger: 50000
    coder: 150000
    explorer: 30000
    security_auditor: 50000
    bug_auditor: 50000
    performance_auditor: 30000
    test_auditor: 30000

context:
  max_history_tokens: 50000
  compaction_threshold: 3000
  compaction_model: claude-haiku-4-5-20251001

safety:
  blocked_commands: ["rm -rf /", "rm -rf ~", "sudo", "mkfs", "> /dev/sda", "chmod 777", ":(){:|:&};:"]
  review_patterns: ["rm ", "pip install", "git push", "git reset --hard", "docker", "kill"]
  allowed_write_paths: ["."]

agent:
  max_iterations: 15
  bash_timeout: 30
  parallel_audit: true
```

### .env

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...       # optional
```

---

## Core Components — Detailed Specs

### 1. LLM Client (`src/core/llm_client.py`)

Wraps the Anthropic Messages API with tool use support.

**Input:**
- model: string (e.g. "claude-sonnet-4-20250514")
- system: string (system prompt)
- messages: list of {role, content} dicts — standard Anthropic format
- tools: list of tool definitions (optional)
- max_tokens: int (default 4096)

**Output — LLMResponse dataclass:**
- content: str — text content from response
- tool_calls: list of {id, name, input} dicts
- stop_reason: str — "end_turn" or "tool_use"
- input_tokens: int
- output_tokens: int
- model: str

**API call:**
```python
response = self.client.messages.create(
    model=model,
    max_tokens=max_tokens,
    system=system,
    messages=messages,
    tools=tools or [],
)
```

**Response parsing:**
- Iterate response.content blocks
- type "text" → append to content string
- type "tool_use" → append {id: block.id, name: block.name, input: block.input} to tool_calls
- Token usage from response.usage.input_tokens and response.usage.output_tokens

---

### 2. ReAct Agent Loop (`src/core/agent_loop.py`)

Base class all agents inherit from. Runs the Think → Act → Observe loop.

**BaseAgent has:**
- name: str (identifier for tracking)
- system_prompt: str (defines agent behavior)
- get_model() → str (which model to use)
- get_tool_names() → list[str] (which tools this agent can access)

**run(task, context) flow:**
```
1. Build initial message: combine context + task into first user message
2. Get tool schemas for this agent's allowed tools
3. Loop up to max_iterations:
   a. Check token budget — stop if hard cap reached
   b. Call LLM with system prompt, messages, tools
   c. Track tokens via token_tracker.add()
   d. If stop_reason == "end_turn" → return AgentResult with content
   e. If stop_reason == "tool_use":
      - For each tool_call: execute via registry, get result string
      - Compact result if too long (via context_manager)
      - Append assistant response + tool results to messages
      - Continue loop
4. If max_iterations reached → return "[Max iterations reached]"
```

**Message format for tool results (Anthropic API):**
```python
# After a tool_use response, append:
messages.append({"role": "assistant", "content": response.content})  # raw content blocks
messages.append({
    "role": "user",
    "content": [{
        "type": "tool_result",
        "tool_use_id": tool_call["id"],
        "content": result_string
    }]
})
```

**AgentResult dataclass:**
- content: str
- agent_name: str
- iterations: int
- total_input_tokens: int
- total_output_tokens: int

---

### 3. Token Tracker (`src/core/token_tracker.py`)

Already implemented in scaffold. Tracks per-agent usage, calculates USD cost, fires warnings, enforces hard cap.

**Pricing (per 1M tokens):**
- Sonnet: $3.00 input / $15.00 output
- Haiku: $0.80 input / $4.00 output

**Key methods:**
- add(agent_name, input_tokens, output_tokens, model) — record usage
- is_hard_cap_reached() → bool
- is_agent_cap_reached(agent_name) → bool
- get_summary() → dict with totals and per-agent breakdown

---

### 4. Context Manager (`src/core/context_manager.py`)

Two mechanisms to protect the context window:

**Tool output compaction:**
- Estimate token count of tool output (len(text) // 4)
- If above compaction_threshold (default 3000 tokens): summarize via Haiku
- Summarization prompt: "Summarize this tool output concisely, preserving all key information including file paths, function names, error messages, and important values:\n\n{output}"
- Return summary instead of raw output

**Sliding window (trim_history):**
- Estimate total tokens across all messages
- If over max_history_tokens: keep first message (original task) + last N messages
- Insert a system note: "[Earlier context was trimmed to save space]"
- Drop middle messages

---

### 5. Safety Checker (`src/core/safety.py`)

Already implemented. Classifies bash commands into three levels:

- BLOCKED: command contains any string from blocked_commands list → reject, never run
- REVIEW: command contains any string from review_patterns list → ask user for confirmation
- SAFE: everything else → execute immediately

File path validation: block writes outside allowed_write_paths (resolve to absolute path, check containment).

---

### 6. Tools (`src/tools/`)

All tools are already implemented in the scaffold. Each tool is a Tool dataclass with name, description, input_schema (JSON Schema), and async handler function.

**bash** — runs shell commands with safety check + timeout
**read_file** — reads file contents with line numbers, or lists directory tree (2 levels deep)
**edit_file** — search/replace, old_str must appear exactly once in file
**write_file** — creates new files, refuses overwrite unless flag set

**Tool schemas** are generated by the registry in Anthropic's tool format:
```python
{"name": "bash", "description": "...", "input_schema": {"type": "object", "properties": {...}, "required": [...]}}
```

---

## Agents — Detailed Specs

### Orchestrator (`src/agents/orchestrator.py`)

The conductor. Does NOT inherit from BaseAgent — it coordinates other agents.

**handle(user_input) flow:**

1. **Classify intent:**
   - Starts with "audit" → audit mode, extract target path
   - Everything else → coding mode (or direct question)

2. **Audit mode — _run_audit(target_path):**
   ```
   explorer_result = await explorer.run(f"Map the codebase at {target_path}")

   # Run all 4 auditors in parallel
   audit_results = await asyncio.gather(
       security_auditor.run("Scan for security issues", context=explorer_result.content),
       bug_auditor.run("Scan for bugs", context=explorer_result.content),
       performance_auditor.run("Scan for performance issues", context=explorer_result.content),
       test_auditor.run("Run tests and check coverage", context=explorer_result.content),
   )

   return format_audit_report(audit_results)
   ```

3. **Coding mode — _run_coding_task(task):**
   ```
   explorer_result = await explorer.run(task)
   challenger_result = await challenger.run(task, context=explorer_result.content)
   coder_result = await coder.run(task, context=challenger_result.content)

   # Auto-audit: run audit pipeline on changed files
   audit_result = await _run_audit(".")

   # If critical findings, attempt auto-fix
   if has_critical_findings(audit_result):
       fix_result = await coder.run(f"Fix these issues:\n{audit_result}")

   return synthesize_final_response(coder_result, audit_result)
   ```

4. **Report formatting:**
   - Parse FINDING blocks from each auditor's response
   - Sort by severity: CRITICAL → HIGH → MEDIUM → LOW
   - Number each finding (for "fix finding #3" command)
   - Display with Rich formatting (colored severity labels)

---

### Explorer (`src/agents/explorer.py`)

**Model:** Haiku (cheap — this is mostly tool calls)
**Tools:** read_file, bash
**System prompt purpose:** Map project structure, read key files, produce a COMPACT summary. No suggestions or fixes.
**Output:** Structured text summary of project type, key files, relevant code sections.

### Challenger (`src/agents/challenger.py`)

**Model:** Sonnet
**Tools:** read_file (to verify assumptions)
**System prompt purpose:** Poke holes in the plan — edge cases, security concerns, scalability. Approve if solid, suggest improvements if not.
**Output:** Approved plan or improved plan with noted concerns.

### Coder (`src/agents/coder.py`)

**Model:** Sonnet
**Tools:** read_file, edit_file, write_file, bash
**System prompt purpose:** Make surgical code changes via search/replace. Verify changes by running tests. Minimal edits, not full rewrites.
**Output:** Description of changes made + verification results.

### Security Auditor (`src/agents/auditors/security.py`)

**Model:** Sonnet
**Tools:** read_file, bash
**Scans for:** SQL injection, command injection, XSS, hardcoded secrets, missing auth, missing input validation, insecure configs, path traversal.
**Output format per finding:**
```
FINDING: <title>
SEVERITY: CRITICAL | HIGH | MEDIUM | LOW
FILE: <path>
LINE: <number>
DESCRIPTION: <explanation>
SUGGESTION: <fix>
```

### Bug Auditor (`src/agents/auditors/bugs.py`)

**Model:** Sonnet
**Tools:** read_file, bash
**Scans for:** Unhandled None, off-by-one, type mismatches, missing error handling, logic errors, resource leaks, dead code.
**Output format:** Same FINDING format as security.

### Performance Auditor (`src/agents/auditors/performance.py`)

**Model:** Haiku
**Tools:** read_file
**Scans for:** O(n²) patterns, N+1 queries, unnecessary allocations, blocking I/O, redundant computations, dead code.
**Output format:** Same FINDING format.

### Test Auditor (`src/agents/auditors/tests.py`)

**Model:** Haiku
**Tools:** read_file, bash
**Workflow:** Find test files → run pytest → analyze results → identify untested paths.
**Output format:** Same FINDING format.

---

## CLI Interface (`src/cli.py`)

**Rich-powered terminal UI with:**
- Banner on startup showing project name and available commands
- `argus >` prompt for input
- Commands: free-form text, `audit <path>`, `fix <finding_id>`, `stats`, `exit`
- Agent status display: show which agents are running (Rich Status or Live)
- Token stats panel: total tokens, cost, percentage of budget used, per-agent breakdown
- Audit report display: colored severity labels (CRITICAL=red, HIGH=yellow, MEDIUM=cyan, LOW=dim)
- Response display: formatted markdown output

---

## Demo Project (`demo/buggy_app/`)

Flask app with 5 planted bugs for testing:

1. **Hardcoded API key** — `API_KEY = "sk-prod-..."` in source code (security)
2. **SQL injection** — `f"SELECT * FROM users WHERE name = '{name}'"` (security)
3. **O(n²) loop** — nested loop computing pairwise scores (performance)
4. **Unhandled None** — `data["value"]` without checking key exists (bugs)
5. **Failing test** — test expects 400 status but app returns 500 on KeyError (tests)

---

## Docker Deployment

**Dockerfile:** Python 3.12-slim, install git, pip install requirements, copy source. Entrypoint runs main.py. Project directory mounted as /project volume.

**docker-compose.yml:** Single service, env_file for .env, mounts argus.yaml read-only and project directory as volume. stdin_open + tty for interactive terminal.

**Usage:**
```bash
cp .env.example .env
# Add API key to .env
PROJECT_PATH=/path/to/your/project docker compose up
```

---

## Build Order

### Phase 1 — Foundation (make a single agent work)
1. Implement llm_client.py chat() method — call Anthropic API, parse response
2. Wire up agent_loop.py ReAct loop — LLM call → tool execution → loop or yield
3. Connect tools via registry
4. Simple CLI loop: input → single agent → display response
5. **Test:** Can the agent read a file, edit it, and run a bash command in one session?

### Phase 2 — Multi-Agent Audit
1. Implement orchestrator routing (detect "audit" command)
2. Wire explorer → parallel auditors via asyncio.gather
3. Parse and rank findings from auditor outputs
4. Display formatted audit report in CLI
5. **Test:** `audit demo/buggy_app` finds all 5 planted bugs

### Phase 3 — Self-Auditing Coding
1. Wire coding pipeline: Explorer → Challenger → Coder
2. After Coder finishes, trigger auto-audit on changed files
3. If critical findings, run Coder again to fix
4. Implement context_manager compaction (summarize long tool outputs)
5. **Test:** Give a coding task, verify auto-audit catches issues in generated code

### Phase 4 — Polish
1. Rich TUI: live agent status panels, colored output, token dashboard
2. Verify Docker build and run works
3. README with architecture diagram, usage examples, demo instructions
4. End-to-end test of full flow

---

## Key Implementation Notes

- Use `anthropic` Python SDK, not raw HTTP requests
- All agent execution is async — use `await` and `asyncio.gather` for parallel agents
- The orchestrator is NOT an LLM agent — it's Python logic that routes and coordinates. It doesn't need its own ReAct loop.
- Each sub-agent is stateless: receives context as input, returns result, done. No persistent memory between agent runs.
- Tool results must be sent back in Anthropic's tool_result format for the ReAct loop to work
- The agent decides to stop (yield to user) when the LLM returns stop_reason="end_turn" instead of "tool_use"
- Token tracking happens at the llm_client level — every API call reports tokens back
- All model names, budgets, and safety rules come from argus.yaml — never hardcode
