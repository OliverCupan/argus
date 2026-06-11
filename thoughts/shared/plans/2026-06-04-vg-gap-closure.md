---
date: 2026-06-04T00:00:00
planner: GIVERNY
research_doc: thoughts/shared/research/2026-06-04-agent-workflow-efficiency.md
grading_source: VG grading assessment (session, 2026-06-04)
status: approved
iteration: 2
---

# Plan: VG Gap Closure

## Objective
Close every gap identified in the LLM grading pass so the build satisfies all 9 features,
all hard gates, and the substance gate at the live demo.

## Research Reference
Based on: grading assessment produced in this session (2026-06-04).
Key gaps identified:
1. SQL injection bug was accidentally fixed in demo app — README claims 5 bugs, only 4 exist
2. README.md project structure is stale (lists 7 deleted agent class files)
3. README.md model table is stale (shows all Haiku — now Coder/Challenger on Sonnet)
4. Spinner shows phase status only — no live cost → VG.3 "real-time" qualifier is weak
5. No demo script documented — examiner has no guided path for the live demo

Non-implementable gaps (examiner-side, noted for student):
- VG-HG-4: student must run the live demo
- VG-HG-1: examiner must confirm spec was approved
- Goldcoin count: student must declare

---

## Phase 1: Restore SQL Injection Bug in Demo App

**Problem:** `demo/buggy_app/app.py:49` currently reads:
```python
cursor = conn.execute("SELECT * FROM users WHERE name = ?", (name,))
```
The comment even says "Fixed: Use parameterized query." This is NOT a bug. The README
and file docstring claim 5 bugs including "SQL injection in /users endpoint." Only 4 bugs
are actually present. The Security Auditor will not find the SQL injection in a demo,
making the system look weaker than documented.

**Change:** Replace the parameterized query with a format-string query (the actual bug).
Remove the "Fixed:" comment.

### Sandbox
Files to modify:
- `demo/buggy_app/app.py` (lines 47–50 only — the /users route DB call)

Files to read (reference):
- `demo/buggy_app/app.py` (full file — confirm exact line content before editing)

### Success Criteria
- [ ] `/users` endpoint uses string interpolation: `f"SELECT * FROM users WHERE name = '{name}'"` (or equivalent vulnerable form)
- [ ] The "Fixed: Use parameterized query" comment is removed
- [ ] The file docstring still lists "SQL injection" as Bug 1
- [ ] No other code in app.py is changed
- [ ] `demo/buggy_app/test_app.py` tests still pass (SQL injection doesn't affect test coverage)

### Verification
Manual:
- [ ] Read the modified lines and confirm they form a real injection vulnerability
- [ ] The other 4 bugs are still present (hardcoded key line 18, O(n²) lines 65-68, None check line 79, failing test in test_app.py)

---

## Phase 2: Fix README.md — Stale Project Structure and Model Table

**Problem A:** `README.md:193–203` project structure lists:
- `src/agents/explorer.py` — deleted in Phase 3 refactor
- `src/agents/coder.py` — deleted
- `src/agents/challenger.py` — deleted
- `src/agents/auditors/security.py` — deleted
- `src/agents/auditors/bugs.py` — deleted
- `src/agents/auditors/performance.py` — deleted
- `src/agents/auditors/tests.py` — deleted

A non-author following this structure would find none of these paths exist. This is a
concrete "not idiot-proof" failure for VG.7.

**Problem B:** `README.md:40–49` model table shows all agents on Haiku. Since Phase 1
(this session), Coder and Challenger now use `claude-sonnet-4-6`.

### Sandbox
Files to modify:
- `README.md` (project structure section + model table section only)

Files to read (reference):
- `src/agents/definitions.py` (verify current agent names + model_keys)
- `argus.yaml` (verify current model assignments)

### Success Criteria
- [ ] Project structure section shows `src/agents/definitions.py` instead of 7 individual agent files
- [ ] Project structure is accurate and navigable for a non-author
- [ ] Model table shows: Coder → `claude-sonnet-4-6`, Challenger → `claude-sonnet-4-6`, all others → `claude-haiku-4-5-20251001`
- [ ] No other README content is changed

### Verification
Manual:
- [ ] Every path listed in README project structure actually exists on disk
- [ ] Model table matches `argus.yaml` exactly

---

## Phase 3: Add Live Cost to Spinner (VG.3 Real-Time Display)

**Problem:** `cli.py:120` — the spinner line is:
```python
line = f"\r  {frame}  {self._current_status}"
```
During execution (which can take 30–120 seconds), the user sees only the phase name.
Cost only appears after the entire pipeline completes. The grading rubric specifies
"token/USD cost is shown in real time." A strict reading requires cost to update
during execution. Without this, VG.3 gets a qualifier that weakens the grade.

**Change:** Append running cost to the spinner line. The `_spinner_task` function has
closure access to `self.token_tracker` via the `_run_agent` method's `self` reference.
Call `self.token_tracker.get_summary()` on each spinner tick and append the cost.

Target spinner format:
```
  ⠙  Explorer mapping codebase…  [$0.0023 · 1,204 tokens]
```

This updates every 0.12 seconds as the TokenTracker accumulates tokens — genuinely real-time.

### Sandbox
Files to modify:
- `src/cli.py` (lines 115–123 only — the `_spinner_task` inner function)

Files to read (reference):
- `src/cli.py` (full `_run_agent` method to understand the closure scope)
- `src/core/token_tracker.py` (`get_summary()` return shape)

### Success Criteria
- [ ] Spinner line includes live cost (`$X.XXXX`) and token count, updating every tick
- [ ] `_SPINNER_WIDTH` increased from 88 to 120 so the longer line fits without truncation
- [ ] The status message is NOT truncated — cost appended after it
- [ ] Spinner still clears correctly on completion (the `"\r" + " " * _SPINNER_WIDTH + "\r"` wipe covers the full 120-char line)
- [ ] No other CLI behavior changes

### Verification
Manual:
- [ ] Run `python main.py`, issue `audit demo/buggy_app` — confirm cost appears and updates in spinner during execution
- [ ] Spinner clears cleanly after completion (no leftover characters)

---

## Phase 4: Create DEMO.md — Exact Demo Script for Examiner

**Problem:** The grader has no guided path through the demo. Without a script, the student
may forget to demonstrate a key feature under exam pressure (e.g., blocked command,
budget display). The grading rubric requires: parallel auditors visible, findings report,
coding task pipeline, cost display, safety gate blocking.

**Change:** Create `DEMO.md` at the repo root with a precise, sequenced demo script
covering every VG criterion that needs live demonstration.

The script must cover:
- Setup (start argus, show `model` command to prove Sonnet on Coder/Challenger)
- VG.1 (parallel auditors): `audit demo/buggy_app` — show spinner cycling through all 4 auditors simultaneously, then findings report
- VG.3 (real-time cost): point to spinner cost updating live during audit
- VG.3 (budget + hard cap): show `budget` command after the run
- VG.4 (safety): attempt `rm -rf /` from within a bash call — show BLOCKED; then show a REVIEW-level command with y/N prompt
- VG.5 (bash): already covered in audit (TestAuditor runs pytest via bash)
- VG.6 (partial edit): run a coding task that triggers `edit_file` — confirm it's a search-replace, not whole-file overwrite
- VG.9 (autonomy): point to the model deciding `end_turn` vs. tool call in the output
- VG.2 (context): explain compaction thresholds (can point to argus.yaml lines 36-39)
- `stats` command: show per-agent token/cost breakdown
- `fix <id>`: demonstrate fixing a specific finding

### Sandbox
Files to create:
- `DEMO.md` (new file at repo root)

Files to read (reference):
- `README.md` (avoid duplicating content)
- Grading template §2 (VG.1–VG.9 criteria — already in this session context)

### Success Criteria
- [ ] `DEMO.md` exists at repo root
- [ ] `DEMO.md` is listed in `.gitignore` (not committed to repo)
- [ ] Every VG criterion (VG.1–VG.9) has an explicit demo step
- [ ] Each step has the exact command to type
- [ ] Each step says what to point to / what the grader should see
- [ ] Blocked command demo is included (shows BLOCKED before subprocess)
- [ ] REVIEW command demo is included (shows y/N prompt)
- [ ] Document is ≤ 150 lines — dense, not padded

### Verification
Manual:
- [ ] Read DEMO.md and confirm every step can actually be executed in `python main.py`
- [ ] The command sequence is in the right order (audit before fix, stats after at least one task)
- [ ] `git status` does not show DEMO.md as tracked or untracked (it is gitignored)

---

## Phase 5: Final Validation Pass

### Sandbox
Files to read:
- `demo/buggy_app/app.py`
- `demo/buggy_app/test_app.py`
- `README.md`
- `DEMO.md`

### Success Criteria
- [ ] `pytest tests/test_argus.py -m unit -x -q` — all pass (69/69)
- [ ] `python -c "from src.agents.orchestrator import Orchestrator; print('OK')"` — passes
- [ ] Every file path in README project structure exists on disk
- [ ] README model table matches `argus.yaml`
- [ ] `demo/buggy_app/app.py` contains a real SQL injection vulnerability in `/users`
- [ ] DEMO.md covers all 9 VG criteria
- [ ] Spinner width constant `_SPINNER_WIDTH` is large enough to show cost without truncation

---

## Rollback Plan
1. Phase 1: `git checkout demo/buggy_app/app.py`
2. Phase 2: `git checkout README.md`
3. Phase 3: `git checkout src/cli.py`
4. Phase 4: `rm DEMO.md`

All phases are independent — any can be reverted without affecting the others.

