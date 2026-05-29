"""
Orchestrator — the conductor of Argus.

Receives user input, classifies intent, delegates to sub-agents,
synthesizes results, decides when to yield back to user.

Two modes:
1. Coding mode: Explorer → Challenger → Coder → Auto-Audit
2. Audit mode: Parallel (Security + Bugs + Performance + Tests) → Ranked report
"""

import asyncio

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


class Orchestrator:
    def __init__(self, config: ArgusConfig, token_tracker: TokenTracker):
        self.config = config
        self.tracker = token_tracker
        self.llm = LLMClient(config)
        self.tools = build_registry(config)

        # Initialize sub-agents
        self.explorer = Explorer(config, self.llm, self.tracker, self.tools)
        self.challenger = Challenger(config, self.llm, self.tracker, self.tools)
        self.coder = Coder(config, self.llm, self.tracker, self.tools)
        self.auditors = [
            SecurityAuditor(config, self.llm, self.tracker, self.tools),
            BugAuditor(config, self.llm, self.tracker, self.tools),
            PerformanceAuditor(config, self.llm, self.tracker, self.tools),
            TestAuditor(config, self.llm, self.tracker, self.tools),
        ]

    async def handle(self, user_input: str) -> str:
        """
        Main entry point. Classify intent and delegate.

        Returns:
            Final response string to display to user.
        """

        # TODO: Implement intent classification and delegation
        #
        # 1. Detect mode:
        #    - if user_input starts with "audit" → audit mode
        #    - else → coding mode (or direct question)
        #
        # 2. Audit mode:
        #    result = await self._run_audit(target_path)
        #
        # 3. Coding mode:
        #    result = await self._run_coding_task(user_input)
        #
        # 4. Return formatted result

        raise NotImplementedError("Implement orchestrator routing")

    async def _run_audit(self, target_path: str) -> str:
        """
        Run parallel audit agents on target path.

        1. Explorer maps the codebase
        2. All 4 auditors run in parallel with explorer's context
        3. Synthesize and rank findings
        """

        # Step 1: Explore
        # explorer_result = await self.explorer.run(f"Map the codebase at {target_path}")

        # Step 2: Parallel audit
        # if self.config.agent.parallel_audit:
        #     audit_results = await asyncio.gather(*[
        #         auditor.run("Audit this code", context=explorer_result.content)
        #         for auditor in self.auditors
        #     ])
        # else:
        #     audit_results = []
        #     for auditor in self.auditors:
        #         result = await auditor.run("Audit this code", context=explorer_result.content)
        #         audit_results.append(result)

        # Step 3: Synthesize
        # return self._format_audit_report(audit_results)

        raise NotImplementedError("Implement audit pipeline")

    async def _run_coding_task(self, task: str) -> str:
        """
        Run the coding pipeline with self-audit.

        1. Explorer maps relevant files
        2. Challenger reviews the approach
        3. Coder implements
        4. Auto-audit on changes
        5. Auto-fix if issues found
        """

        # Step 1: Explore
        # explorer_result = await self.explorer.run(task)

        # Step 2: Challenge
        # challenge_result = await self.challenger.run(task, context=explorer_result.content)

        # Step 3: Code
        # coder_result = await self.coder.run(task, context=challenge_result.content)

        # Step 4: Auto-audit the changes
        # audit_result = await self._run_audit(".")

        # Step 5: If issues found, attempt auto-fix
        # ...

        raise NotImplementedError("Implement coding pipeline")

    def _format_audit_report(self, results: list[AgentResult]) -> str:
        """Format audit results into a ranked report."""

        # TODO: Parse findings from each auditor, rank by severity, format nicely
        raise NotImplementedError("Implement report formatting")
