# Argus

**Multi-agent coding assistant with self-auditing superpowers.**

Argus is a terminal-based AI coding assistant that doesn't just write code — it hunts bugs in its own output before you see the result. Parallel specialist agents (Security, Bugs, Performance, Tests) audit every change automatically, while a Challenger agent pokes holes in the plan before a single line is written.

---

## Architecture

```
User Input
    │
    ▼
┌───────────────┐
│  Orchestrator │  routes intent, synthesises results
└───────┬───────┘
        │
        ├── Coding task ──────────────────────────────────────────────────┐
        │                                                                  │
        │   ┌──────────┐   ┌────────────┐   ┌──────────┐                 │
        │   │ Explorer │ → │ Challenger │ → │  Coder   │                 │
        │   │ maps code│   │ pokes holes│   │ edits/   │                 │
        │   └──────────┘   └────────────┘   │ verifies │                 │
        │                                   └──────────┘                 │
        │                                        │                        │
        │                   Auto-Audit ──────────┘                        │
        │                                                                  │
        └── audit <path> ──────────────────────────────────────────────── ┘
                │
                │   (parallel)
                ├── SecurityAuditor  — injection, secrets, auth
                ├── BugAuditor       — logic errors, null refs, type mismatches
                ├── PerformanceAuditor — O(n²), N+1 queries, blocking I/O
                └── TestAuditor      — run pytest, spot untested paths
```

### Agent roles

| Agent | Model | Role |
|---|---|---|
| Orchestrator | claude-haiku-4-5-20251001 | Routes intent, coordinates pipeline |
| Explorer | claude-haiku-4-5-20251001 | Maps codebase, reads relevant files |
| Challenger | claude-sonnet-4-6 | Critiques the plan before coding starts |
| Coder | claude-sonnet-4-6 | Makes surgical edits, verifies with tests |
| SecurityAuditor | claude-haiku-4-5-20251001 | Finds injection, secrets, auth issues |
| BugAuditor | claude-haiku-4-5-20251001 | Finds logic errors, null refs, type mismatches |
| PerformanceAuditor | claude-haiku-4-5-20251001 | Finds O(n²), N+1 queries, blocking I/O |
| TestAuditor | claude-haiku-4-5-20251001 | Runs pytest, spots untested paths |

---

## Quick Start

### Run directly

```bash
# 1. Clone
git clone https://github.com/OliverCupan/argus.git
cd argus

# 2. Configure
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 3. Install
pip install -r requirements.txt

# 4. Run
python main.py
```

### Run with Docker (Web GUI)

```bash
# Starts the web interface at http://localhost:7777
cp .env.example .env
# Add ANTHROPIC_API_KEY to .env

# Point at any project directory
PROJECT_PATH=/path/to/your/project docker compose up
# Then open http://localhost:7777 in your browser
```

---

## Usage

```
argus > fix the login endpoint to validate email format
argus > add rate limiting to the /api/search endpoint
argus > audit src/
argus > fix 3
argus > stats
argus > exit
```

### Commands

| Command | Description |
|---|---|
| `<free-form text>` | Coding task: Explorer → Challenger → Coder → Auto-Audit |
| `audit <path>` | Audit a directory: parallel Security/Bug/Perf/Test scan |
| `fix <id>` | Fix a specific finding from the last audit (e.g. `fix 3`) |
| `stats` | Show per-agent token usage and USD cost |
| `budget` | Show token/USD caps and current usage |
| `budget set <field> <value>` | Adjust a cap live (e.g. `budget set dollar_hard_cap 10`) |
| `model` | Show which model each agent is using |
| `model <agent> <name>` | Switch an agent's model live (e.g. `model coder claude-opus-4-8`) |
| `exit` | Quit |

### Safety

Bash commands are classified before execution:

- **BLOCKED** — `rm -rf /`, `sudo`, `mkfs`, etc. → rejected automatically
- **REVIEW** — `rm`, `pip install`, `git push`, `docker`, etc. → requires your `y/N` confirmation
- **SAFE** — `ls`, `cat`, `grep`, `pytest`, etc. → runs immediately

---

## Configuration

All settings live in `argus.yaml` — no hardcoded values in source.

```yaml
models:
  orchestrator: claude-haiku-4-5-20251001
  coder: claude-haiku-4-5-20251001
  # ...

token_budget:
  total_hard_cap: 500000      # hard stop at this many tokens
  warning_threshold: 0.8      # warn at 80%
  per_agent:
    coder: 150000

safety:
  blocked_commands: ["rm -rf /", "sudo", ...]
  review_patterns: ["pip install", "git push", ...]
  allowed_write_paths: ["."]  # agents can only write inside project dir
```

---

## Development

```bash
# Debug logging
ARGUS_LOG_LEVEL=DEBUG python main.py

# Run tests
pytest

# Lint
ruff check src/
```

## Project Structure

```
argus/
├── main.py                 # Entry point
├── argus.yaml              # All configuration
├── .env.example            # API key template
├── Dockerfile
├── docker-compose.yml
├── src/
│   ├── cli.py              # Terminal UI
│   ├── config.py           # YAML + env loader
│   └── core/
│       ├── llm_client.py   # Anthropic API wrapper
│       ├── agent_loop.py   # ReAct base loop
│       ├── token_tracker.py
│       ├── context_manager.py
│       └── safety.py
│   └── agents/
│       ├── orchestrator.py
│       └── definitions.py      # All 7 agent definitions as AgentDefinition dataclasses
```
