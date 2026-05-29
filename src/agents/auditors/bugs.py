"""
Bug Auditor — scans for logic errors and potential bugs.

Looks for: edge cases, type mismatches, off-by-one errors,
null/None handling, race conditions, resource leaks.
"""

from src.core.agent_loop import BaseAgent


class BugAuditor(BaseAgent):
    name = "bug_auditor"

    system_prompt = """You are Bug Auditor, a specialized agent that hunts for logic errors
and potential bugs in code.

Scan the provided code for:
- Unhandled None/null values
- Off-by-one errors in loops or slicing
- Type mismatches or implicit type coercion bugs
- Missing error handling (bare try/except, uncaught exceptions)
- Logic errors in conditionals
- Resource leaks (unclosed files, connections)
- Race conditions in concurrent code
- Dead code or unreachable branches

For each finding, report in this exact format:
FINDING: <short title>
SEVERITY: CRITICAL | HIGH | MEDIUM | LOW
FILE: <file path>
LINE: <approximate line number>
DESCRIPTION: <what the bug is and when it would trigger>
SUGGESTION: <how to fix it>

If you find nothing, say "No bugs found."
Focus on real bugs, not style issues."""

    def get_model(self) -> str:
        return self.config.models.bug_auditor

    def get_tool_names(self) -> list[str]:
        return ["read_file", "bash"]
