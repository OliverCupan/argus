"""
Orchestrator — the conductor of Argus.

Receives user input, classifies intent, delegates to sub-agents,
synthesizes results, and returns a formatted response.

Two modes:
1. Audit mode  (input starts with "audit"):
       Explorer → parallel (Security + Bugs + Performance + Tests) → ranked report

2. Coding mode (everything else):
       Explorer → Challenger → Coder → Auto-Audit → optional auto-fix
"""

import asyncio
import logging
import re

from src.config import ArgusConfig
from src.core.llm_client import LLMClient
from src.core.token_tracker import TokenTracker
from src.core.agent_loop import AgentResult
from src.tools.registry import ToolRegistry, build_registry

from src.agents.explorer import Explorer
from src.agents.challenger import Challenger
from src.agents.coder import Coder
from src.agents.auditors.security import SecurityAuditor
from src.agents.auditors.bugs import BugAuditor
from src.agents.auditors.performance import PerformanceAuditor
from src.agents.auditors.tests import TestAuditor

logger = logging.getLogger(__name__)

# Severity ordering for report sorting
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


class Orchestrator:
    def __init__(
        self,
        config: ArgusConfig,
        token_tracker: TokenTracker,
        confirm_callback=None,
        status_callback=None,
    ):
        """
        Args:
            config: ArgusConfig
            token_tracker: shared TokenTracker
            confirm_callback: async (command) -> bool, for bash REVIEW prompts
            status_callback: async (message: str) -> None, for live status updates in CLI
        """
        self.config = config
        self.tracker = token_tracker
        self.llm = LLMClient(config)
        self.tools = build_registry(config, confirm_callback=confirm_callback)
        async def _noop(msg: str) -> None:
            pass
        self._status = status_callback or _noop

        self.explorer = Explorer(config, self.llm, self.tracker, self.tools)
        self.challenger = Challenger(config, self.llm, self.tracker, self.tools)
        self.coder = Coder(config, self.llm, self.tracker, self.tools)
        self.auditors: list = [
            SecurityAuditor(config, self.llm, self.tracker, self.tools),
            BugAuditor(config, self.llm, self.tracker, self.tools),
            PerformanceAuditor(config, self.llm, self.tracker, self.tools),
            TestAuditor(config, self.llm, self.tracker, self.tools),
        ]

    async def close(self):
        await self.llm.close()

    async def handle(self, user_input: str) -> str:
        """
        Main entry point. Classify intent and delegate.
        Returns formatted response string.
        """
        stripped = user_input.strip()

        # Audit mode: "audit <path>" or "audit" alone (defaults to ".")
        if re.match(r"^audit\b", stripped, re.IGNORECASE):
            parts = stripped.split(maxsplit=1)
            target_path = parts[1].strip() if len(parts) > 1 else "."
            logger.debug("Audit mode: target=%s", target_path)
            return await self._run_audit(target_path)

        # Coding mode: everything else
        logger.debug("Coding mode: task=%s", stripped[:60])
        return await self._run_coding_task(stripped)

    # ------------------------------------------------------------------ #
    #  Audit pipeline                                                       #
    # ------------------------------------------------------------------ #

    async def _run_audit(self, target_path: str) -> str:
        await self._status(f"Explorer mapping {target_path}…")
        explorer_result = await self.explorer.run(
            f"Map the codebase at '{target_path}'. "
            f"List all files, read the main source files, and produce a compact summary "
            f"of what is there so auditors can focus their analysis."
        )
        logger.debug("Explorer done: %d tokens", explorer_result.total_input_tokens + explorer_result.total_output_tokens)

        await self._status("Running 4 auditors in parallel…")

        audit_task = f"Audit the code at '{target_path}'. Context from Explorer:\n\n{explorer_result.content}"

        if self.config.agent.parallel_audit:
            audit_results: list[AgentResult] = list(await asyncio.gather(
                self.auditors[0].run(audit_task),  # security
                self.auditors[1].run(audit_task),  # bugs
                self.auditors[2].run(audit_task),  # performance
                self.auditors[3].run(audit_task),  # tests
            ))
        else:
            audit_results = []
            for auditor in self.auditors:
                await self._status(f"Running {auditor.name}…")
                audit_results.append(await auditor.run(audit_task))

        await self._status("Synthesising findings…")
        return self._format_audit_report(audit_results, target_path)

    # ------------------------------------------------------------------ #
    #  Coding pipeline                                                      #
    # ------------------------------------------------------------------ #

    async def _run_coding_task(self, task: str) -> str:
        await self._status("Explorer mapping codebase…")
        explorer_result = await self.explorer.run(task)

        await self._status("Challenger reviewing approach…")
        challenger_result = await self.challenger.run(
            task, context=explorer_result.content
        )

        await self._status("Coder implementing…")
        coder_result = await self.coder.run(
            task, context=challenger_result.content
        )

        # Auto-audit changed files
        await self._status("Auto-auditing changes…")
        audit_report = await self._run_audit(".")

        # Check for CRITICAL findings — if any, attempt auto-fix
        if "CRITICAL" in audit_report:
            await self._status("Critical findings detected — attempting auto-fix…")
            fix_result = await self.coder.run(
                f"Fix all CRITICAL issues from this audit report:\n\n{audit_report}",
                context=coder_result.content,
            )
            return (
                f"{coder_result.content}\n\n"
                f"---\n\n"
                f"**Auto-fix applied for critical findings:**\n{fix_result.content}\n\n"
                f"---\n\n"
                f"**Audit report:**\n{audit_report}"
            )

        return (
            f"{coder_result.content}\n\n"
            f"---\n\n"
            f"**Auto-audit:**\n{audit_report}"
        )

    # ------------------------------------------------------------------ #
    #  Report formatting                                                    #
    # ------------------------------------------------------------------ #

    def _format_audit_report(
        self, results: list[AgentResult], target_path: str
    ) -> str:
        """
        Parse FINDING blocks from all auditor outputs, sort by severity,
        and format into a numbered markdown report.
        """
        findings = []
        for result in results:
            findings.extend(_parse_findings(result.content, result.agent_name))

        if not findings:
            return f"**Audit of `{target_path}` — No issues found.**\n\nAll auditors returned clean."

        # Sort: CRITICAL → HIGH → MEDIUM → LOW
        findings.sort(key=lambda f: _SEVERITY_RANK.get(f["severity"], 99))

        lines = [f"## Audit Report — `{target_path}`\n"]
        lines.append(f"**{len(findings)} finding(s)** across {len(results)} auditors.\n")

        severity_counts: dict[str, int] = {}
        for f in findings:
            severity_counts[f["severity"]] = severity_counts.get(f["severity"], 0) + 1

        summary_parts = []
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if sev in severity_counts:
                summary_parts.append(f"{severity_counts[sev]} {sev}")
        lines.append("**Summary:** " + " · ".join(summary_parts) + "\n")
        lines.append("---\n")

        for i, f in enumerate(findings, 1):
            sev = f["severity"]
            # Severity label with markdown bold
            sev_label = f"**{sev}**"
            lines.append(f"### Finding #{i} — {f['title']} [{sev_label}]")
            if f.get("file"):
                loc = f["file"]
                if f.get("line"):
                    loc += f":{f['line']}"
                lines.append(f"**Location:** `{loc}`")
            lines.append(f"**Source:** {f['source']}")
            if f.get("description"):
                lines.append(f"\n{f['description']}")
            if f.get("suggestion"):
                lines.append(f"\n**Fix:** {f['suggestion']}")
            lines.append("")

        return "\n".join(lines)


def _parse_findings(text: str, source: str) -> list[dict]:
    """
    Extract structured FINDING blocks from auditor output text.

    Expected format (each block starts with FINDING:):
        FINDING: <title>
        SEVERITY: CRITICAL | HIGH | MEDIUM | LOW
        FILE: <path>
        LINE: <number>
        DESCRIPTION: <text>
        SUGGESTION: <text>
    """
    findings = []
    # Split on FINDING: boundaries
    blocks = re.split(r"(?=^FINDING:)", text, flags=re.MULTILINE)

    for block in blocks:
        block = block.strip()
        if not block.startswith("FINDING:"):
            continue

        def _field(name: str) -> str:
            m = re.search(rf"^{name}:\s*(.+?)(?=\n[A-Z]+:|$)", block, re.MULTILINE | re.DOTALL)
            return m.group(1).strip() if m else ""

        title = _field("FINDING")
        severity = _field("SEVERITY").upper()
        if severity not in _SEVERITY_RANK:
            severity = "LOW"

        findings.append({
            "title": title,
            "severity": severity,
            "file": _field("FILE"),
            "line": _field("LINE"),
            "description": _field("DESCRIPTION"),
            "suggestion": _field("SUGGESTION"),
            "source": source,
        })

    return findings
