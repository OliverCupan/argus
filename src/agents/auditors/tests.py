"""
Test Auditor — runs tests and identifies coverage gaps.

Runs existing test suite, reports failures, spots untested critical paths.
Uses Haiku since this is mostly bash execution work.
"""

from src.core.agent_loop import BaseAgent


class TestAuditor(BaseAgent):
    name = "test_auditor"

    system_prompt = """You are Test Auditor, a specialized agent that evaluates test quality
and coverage.

Your workflow:
1. Look for test files (test_*.py, *_test.py, tests/ directory)
2. Run the test suite (pytest, unittest, etc.)
3. Analyze results: which tests pass, which fail, and why
4. Identify critical code paths that have NO tests

For each finding, report in this exact format:
FINDING: <short title>
SEVERITY: HIGH | MEDIUM | LOW
FILE: <file path>
DESCRIPTION: <what's missing or failing>
SUGGESTION: <what test should be added or how to fix failing test>

Always run the tests before reporting. Report actual test output.
If no test framework is found, report that as a HIGH severity finding."""

    def get_model(self) -> str:
        return self.config.models.test_auditor

    def get_tool_names(self) -> list[str]:
        return ["read_file", "bash"]
