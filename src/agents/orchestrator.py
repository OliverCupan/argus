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
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.config import ArgusConfig
from src.core.worktree import _run as _git
from src.core.llm_client import LLMClient
from src.core.token_tracker import TokenTracker
from src.core.agent_loop import AgentResult
from src.core.worktree import WorktreeManager
from src.tools.registry import ToolRegistry, build_registry

from src.agents.definitions import (
    EXPLORER_DEF, CHALLENGER_DEF, CODER_DEF,
    SECURITY_AUDITOR_DEF, BUG_AUDITOR_DEF,
    PERFORMANCE_AUDITOR_DEF, TEST_AUDITOR_DEF,
)
from src.core.agent_loop import make_agent

if TYPE_CHECKING:
    from src.gui.event_bus import EventBus

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
        event_bus: "Optional[EventBus]" = None,
    ):
        """
        Args:
            config: ArgusConfig
            token_tracker: shared TokenTracker
            confirm_callback: async (command) -> bool, for bash REVIEW prompts
            status_callback: async (message: str) -> None, for live status updates in CLI
            event_bus: optional EventBus for real-time GUI streaming
        """
        self.config = config
        self.tracker = token_tracker
        self.llm = LLMClient(config)
        self.tools = build_registry(config, confirm_callback=confirm_callback, llm=self.llm, tracker=self.tracker, event_bus=event_bus)
        self._event_bus: "Optional[EventBus]" = event_bus

        _user_status = status_callback

        async def _status_with_emit(msg: str) -> None:
            # Forward to the user-supplied callback (if any)
            if _user_status:
                await _user_status(msg)
            # Simultaneously fan-out to the GUI event bus
            if event_bus is not None:
                await event_bus.emit("orchestrator", "status_update", message=msg)

        self._status = _status_with_emit

        # Persists between requests so `fix <id>` can reference the last audit
        self._last_findings: list[dict] = []
        self._orchestrator_lifecycle_done = False
        self._worktrees = WorktreeManager(config.agent)

        _mk = lambda defn: make_agent(defn, config, self.llm, self.tracker, self.tools, event_bus=event_bus)
        self.explorer = _mk(EXPLORER_DEF)
        self.challenger = _mk(CHALLENGER_DEF)
        self.coder = _mk(CODER_DEF)
        self.auditors: list = [
            _mk(SECURITY_AUDITOR_DEF),
            _mk(BUG_AUDITOR_DEF),
            _mk(PERFORMANCE_AUDITOR_DEF),
            _mk(TEST_AUDITOR_DEF),
        ]

    async def _emit(self, agent_name: str, event_type: str, **data) -> None:
        """Emit a GUI event if an event bus is wired; no-op in CLI mode."""
        if agent_name == "orchestrator" and event_type == "task_complete":
            self._orchestrator_lifecycle_done = True
        if self._event_bus is not None:
            await self._event_bus.emit(agent_name, event_type, **data)

    async def close(self):
        await self.llm.close()
        await self._worktrees.cleanup_all()

    def set_model(self, agent_name: str, model: str) -> bool:
        """Update an agent's model at runtime. Returns True on success."""
        if not hasattr(self.config.models, agent_name):
            return False
        setattr(self.config.models, agent_name, model)
        logger.info("Model updated: %s → %s", agent_name, model)
        return True

    async def handle(self, user_input: str) -> str:
        """
        Main entry point. Classify intent and delegate.
        Returns formatted response string.
        """
        stripped = user_input.strip()
        task_preview = stripped[:120]
        self._orchestrator_lifecycle_done = False
        await self._emit("orchestrator", "agent_started", task_preview=task_preview, model=None, tools=[])

        result = "[Task ended early]"
        try:
            routing = await self._classify_intent(stripped)
            mode = routing.get("mode", "code")
            logger.debug("Routing decision: mode=%s task=%s", mode, stripped[:60])

            if mode == "audit":
                target_path = routing.get("target", ".")
                result = await self._run_audit(target_path)
            elif mode == "query":
                result = await self._run_query(stripped)
            else:
                result = await self._run_coding_task(stripped)
            return result
        finally:
            if not self._orchestrator_lifecycle_done:
                _summary = self.tracker.get_summary()
                await self._emit("orchestrator", "agent_finished",
                                 content=result, tokens_in=0, tokens_out=0,
                                 iterations=0, summary=_summary)
                await self._emit("orchestrator", "task_complete",
                                 result_markdown=result, diff_stat="", summary=_summary)

    # ------------------------------------------------------------------ #
    #  Intent classification                                               #
    # ------------------------------------------------------------------ #

    async def _classify_intent(self, task: str) -> dict:
        """
        Use a fast LLM call to classify intent into: audit | query | code.
        Falls back to 'code' on any failure — safe default.
        """
        try:
            response = await self.llm.chat(
                model=self.config.models.orchestrator,
                system=(
                    "You are a task router for a coding assistant. "
                    "Classify the user request into one of three modes:\n"
                    "- audit: scan/analyze existing code for issues "
                    "(e.g. 'audit src/', 'check for bugs', 'scan for security issues')\n"
                    "- query: read-only question about code, no changes needed "
                    "(e.g. 'what does X do', 'where is Y', 'list files', 'explain', 'show me')\n"
                    "- code: write, edit, fix, refactor, or add code "
                    "(e.g. 'add X', 'fix Y', 'implement Z', 'refactor')\n"
                    "For audit mode, also extract the target path from the request. "
                    "Default target is '.' if no path is mentioned."
                ),
                messages=[{"role": "user", "content": task}],
                tools=[{
                    "name": "route",
                    "description": "Output the routing decision",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": ["audit", "query", "code"],
                                "description": "Which pipeline to run",
                            },
                            "target": {
                                "type": "string",
                                "description": "Path for audit mode (default '.')",
                            },
                        },
                        "required": ["mode"],
                    },
                }],
                max_tokens=256,
            )
            if response.tool_calls:
                return response.tool_calls[0]["input"]
            # Fallback: parse from content text
            content = (response.content or "").lower()
            if "audit" in content:
                return {"mode": "audit", "target": "."}
            if "query" in content:
                return {"mode": "query"}
            return {"mode": "code"}
        except Exception as exc:
            logger.warning("Intent classification failed (%s) — defaulting to code", exc)
            return {"mode": "code"}

    async def _run_query(self, task: str) -> str:
        """
        Fast path for read-only questions — Explorer only, no Challenger/Coder/audit.
        """
        self.tracker.reset_task_usage()
        await self._status("Explorer answering query…")
        result = await self.explorer.run(task)
        _summary = self.tracker.get_summary()
        await self._emit("orchestrator", "agent_finished",
                         content=result.content, tokens_in=0, tokens_out=0,
                         iterations=0, summary=_summary)
        await self._emit("orchestrator", "task_complete",
                         result_markdown=result.content, diff_stat="",
                         summary=_summary)
        return result.content

    # ------------------------------------------------------------------ #
    #  Audit pipeline                                                       #
    # ------------------------------------------------------------------ #

    async def _run_audit(
        self,
        target_path: str,
        explorer_context: str = "",
        auditor_filter: "Optional[list[str]]" = None,
        coder_test_output: str = "",
        specific_files: "Optional[list[str]]" = None,
    ) -> str:
        """
        Run the audit pipeline.

        Args:
            target_path: Path to audit (directory or file). Ignored for path-exists check
                         when specific_files is provided.
            explorer_context: Pre-built context to inject (skips Explorer run if set).
            auditor_filter: If set, only run auditors whose .name is in this list.
                            None = run all (used for explicit `audit` commands).
            coder_test_output: Test output from a prior Coder run; passed to Test auditor
                               so it can skip redundant re-runs.
            specific_files: Explicit list of changed files (used for auto-audit from
                            coding pipeline). When provided, skips path-exists check and
                            uses a targeted audit_task instead of the directory-scoped one.
        """
        import os
        self.tracker.reset_task_usage()

        # Path-existence check — skip when we have a specific file list (auto-audit)
        if specific_files is None:
            resolved = os.path.realpath(target_path)
            if not os.path.exists(resolved):
                return f"**Audit failed:** path `{target_path}` does not exist."

        # Reuse existing context if provided, otherwise run Explorer
        if not explorer_context:
            await self._status(f"Explorer mapping {target_path}…")
            explorer_result = await self.explorer.run(
                f"Map the codebase at '{target_path}'. "
                f"Start by listing and reading files inside '{target_path}' specifically, "
                f"then note other relevant project files. Produce a compact summary "
                f"so auditors can focus their analysis."
            )
            logger.debug("Explorer done: %d tokens", explorer_result.total_input_tokens + explorer_result.total_output_tokens)

            if explorer_result.content.startswith("["):
                logger.warning("Explorer returned no content for audit — using direct file fallback")
                fallback_context = await self._read_files_as_context(target_path)
                if not fallback_context:
                    return f"**Audit of `{target_path}`:** no readable source files found."
                explorer_context = f"[No Explorer summary — file contents below]\n\n{fallback_context}"
            else:
                explorer_context = explorer_result.content
        else:
            logger.debug("Reusing pre-existing context for audit (%d chars)", len(explorer_context))

        # Select auditors — filter when requested (tiered auto-audit)
        active_auditors = [
            a for a in self.auditors
            if auditor_filter is None or a.name in auditor_filter
        ]
        if auditor_filter is not None:
            skipped = [a.name for a in self.auditors if a.name not in auditor_filter]
            if skipped:
                logger.debug("Tiered audit: skipping %s", skipped)
        n_auditors = len(active_auditors)
        await self._status(f"Running {n_auditors} auditor(s) in parallel…")

        # Build base audit task — targeted when specific files are known
        if specific_files:
            file_list = ", ".join(f"`{f}`" for f in specific_files[:15])
            if len(specific_files) > 15:
                file_list += f" (+{len(specific_files) - 15} more)"
            audit_task_str = f"Audit these specific files changed by the Coder: {file_list}."
        else:
            audit_task_str = f"Audit the code at '{target_path}'."

        def _task_for(auditor) -> tuple[str, str]:
            task = audit_task_str
            ctx = explorer_context
            if auditor.name == "test_auditor" and coder_test_output:
                task = (
                    task
                    + f"\n\nIMPORTANT — The Coder already ran the test suite. Output:\n```\n{coder_test_output}\n```\n"
                    + "Only re-run tests if you need to verify or if the above output is incomplete."
                )
            return task, ctx

        if self.config.agent.parallel_audit:
            tasks = [
                asyncio.create_task(a.run(*_task_for(a)), name=a.name)
                for a in active_auditors
            ]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            audit_results: list[AgentResult] = []
            for auditor, result_or_exc in zip(active_auditors, raw_results):
                if isinstance(result_or_exc, Exception):
                    logger.error("Auditor %s failed: %s", auditor.name, result_or_exc)
                    audit_results.append(AgentResult(
                        content=f"FINDING: Auditor failed\nSEVERITY: LOW\nDESCRIPTION: {result_or_exc}\nSUGGESTION: Check logs.",
                        agent_name=auditor.name,
                        iterations=0,
                    ))
                else:
                    audit_results.append(result_or_exc)
            await self._status(f"All {n_auditors} auditor(s) done…")
        else:
            audit_results = []
            for auditor in active_auditors:
                await self._status(f"Running {auditor.name}…")
                audit_results.append(await auditor.run(*_task_for(auditor)))

        await self._status("Synthesising findings…")
        # Display label for report header
        if specific_files:
            display_target = ", ".join(specific_files[:5])
            if len(specific_files) > 5:
                display_target += f" (+{len(specific_files) - 5} more)"
        else:
            display_target = target_path
        report = self._format_audit_report(audit_results, display_target)
        _summary = self.tracker.get_summary()
        await self._emit("orchestrator", "agent_finished",
                         content=report, tokens_in=0, tokens_out=0,
                         iterations=0, summary=_summary)
        await self._emit("orchestrator", "task_complete", result_markdown=report, diff_stat="",
                         summary=_summary)
        return report

    # ------------------------------------------------------------------ #
    #  Coding pipeline                                                      #
    # ------------------------------------------------------------------ #

    async def _run_coding_task(self, task: str) -> str:
        self.tracker.reset_task_usage()

        # Phase 1: Explorer maps the codebase — focus on paths mentioned in task
        path_hints = self._extract_path_hints(task)
        if path_hints:
            hint_str = ", ".join(f"'{h}'" for h in path_hints[:5])
            explorer_prompt = (
                f"Focus on these paths first: {hint_str}. "
                f"Read those files carefully, then briefly describe the rest of the project. "
                f"Task context: {task}"
            )
        else:
            explorer_prompt = task
        await self._status("Explorer mapping codebase…")
        explorer_result = await self.explorer.run(explorer_prompt)

        # Phase 2: Challenger reviews the approach
        # NOTE: we deliberately do NOT start a Coder pre-read task here because:
        # a) Coder has write-tool access and shouldn't run speculatively with edits, and
        # b) the Challenger plan (which Coder receives as context) may redirect the Coder
        #    to different files than Explorer identified — pre-reading the wrong files wastes tokens.
        await self._status("Challenger reviewing approach…")
        challenger_result = await self.challenger.run(task, context=explorer_result.content)

        # Phase 3: Coder implements using Challenger's reviewed plan
        # Snapshot dirty files BEFORE the Coder runs so we can isolate its changes.
        pre_dirty = set(await self._get_dirty_files())

        # Snapshot mtimes for ALL dirty files + path hints — git dirty-set diff
        # misses edits to files that were already dirty/untracked.
        pre_mtimes: dict[str, float] = {}
        for f in list(pre_dirty) + path_hints:
            if f in pre_mtimes:
                continue
            hp = Path(f)
            if hp.is_file():
                try:
                    pre_mtimes[f] = hp.stat().st_mtime
                except OSError:
                    pass

        coder_cwd = None
        if self.config.agent.use_worktrees:
            await self._status("Creating Coder worktree…")
            coder_cwd = await self._worktrees.create("coder")
            if coder_cwd:
                logger.info("Coder running in worktree: %s", coder_cwd)
            else:
                logger.warning("Worktree creation failed — Coder will work on main tree")

        await self._status("Coder implementing…")
        # Read the actual file contents for path-hinted files so the Coder
        # can start editing immediately without wasting iterations on reads.
        # Cap total injected file content at 30k chars (same as _read_files_as_context).
        _INJECT_MAX_CHARS = 30_000

        async def _read_or_none(hint: str) -> str | None:
            hp = Path(hint)
            if not hp.is_file():
                return None
            return await asyncio.to_thread(hp.read_text, encoding="utf-8", errors="replace")

        # Dispatch all file reads concurrently, then apply the cap in original order.
        _read_results = await asyncio.gather(
            *(_read_or_none(h) for h in path_hints), return_exceptions=True
        )
        raw_file_sections: list[str] = []
        _inject_total = 0
        for hint, result in zip(path_hints, _read_results):
            if _inject_total >= _INJECT_MAX_CHARS:
                logger.debug("Coder injection cap reached at %d chars — skipping remaining hints", _inject_total)
                break
            if result is None or isinstance(result, BaseException):
                continue
            snippet = result[:_INJECT_MAX_CHARS - _inject_total]
            raw_file_sections.append(f"### {hint}\n```\n{snippet}\n```")
            _inject_total += len(snippet)
        raw_files_block = "\n\n".join(raw_file_sections)

        coder_context_parts = []
        if raw_files_block:
            coder_context_parts.append(
                f"## File Contents (DO NOT re-read these — edit them directly)\n\n{raw_files_block}"
            )
        coder_context_parts.append(
            f"## Implementation Plan\n\n{challenger_result.content}"
        )
        coder_context = "\n\n".join(coder_context_parts)

        if coder_cwd:
            coder_context = (
                f"IMPORTANT: Your working directory for this task is: {coder_cwd}\n"
                f"All file paths should be relative to this directory or use absolute paths inside it.\n\n"
                f"{coder_context}"
            )
        coder_result = await self.coder.run(task, context=coder_context)

        # If Coder hit its token cap mid-task, attempt one continuation run
        if "HANDOFF:" in coder_result.content:
            logger.info("Coder emitted HANDOFF — spawning continuation run")
            await self._status("Coder hit budget limit — continuing…")
            continuation_context = (
                f"Previous run stopped early. Here is the handoff:\n\n"
                f"{coder_result.content}\n\n"
                f"Original task: {task}\n\n"
                f"Continue from where the previous run left off. "
                f"Complete the items listed under 'remaining'."
            )
            continuation = await self.coder.run(
                "Continue the task based on the handoff above.",
                context=continuation_context,
            )
            # Merge: keep original work + continuation (no further retries)
            coder_result = AgentResult(
                content=coder_result.content + "\n\n---\n\n**Continuation:**\n" + continuation.content,
                agent_name=coder_result.agent_name,
                iterations=coder_result.iterations + continuation.iterations,
                total_input_tokens=coder_result.total_input_tokens + continuation.total_input_tokens,
                total_output_tokens=coder_result.total_output_tokens + continuation.total_output_tokens,
            )

        # Merge worktree changes back to main tree
        if coder_cwd:
            await self._status("Merging Coder worktree back…")
            merge_summary = await self._worktrees.merge_back("coder")
            await self._worktrees.cleanup("coder")
            logger.info("Worktree merge: %s", merge_summary)

        # Phase 4: Scope audit and diff to ONLY what the Coder touched this task.
        # Two detection methods (union):
        #   A) Git dirty-set diff — catches newly created/modified files
        #   B) Mtime comparison — catches edits to already-dirty/untracked files
        post_dirty = set(await self._get_dirty_files())
        new_dirty = list(post_dirty - pre_dirty)

        mtime_changed: list[str] = []
        for hint, old_mtime in pre_mtimes.items():
            hp = Path(hint)
            try:
                if hp.is_file() and hp.stat().st_mtime != old_mtime:
                    mtime_changed.append(hint)
            except OSError:
                pass

        coder_touched = list(dict.fromkeys(new_dirty + mtime_changed))
        logger.debug("Coder touched %d file(s): %s (git=%d, mtime=%d)",
                      len(coder_touched), coder_touched, len(new_dirty), len(mtime_changed))

        if coder_touched:
            await self._status(f"Auto-auditing {len(coder_touched)} changed file(s)…")
        else:
            # No files changed — skip the expensive audit entirely
            await self._status("Skipping auto-audit (Coder made no file changes).")

        if coder_touched:
            # Build a focused delta context for auditors: git diff + new file contents.
            # This replaces the stale pre-Coder Explorer summary so auditors see exactly
            # what changed instead of spending iterations re-reading the whole codebase.
            delta_context = await self._build_delta_context(coder_touched)
            if not delta_context:
                # Fallback: use the Explorer summary if delta build failed
                delta_context = explorer_result.content

            # Extract any test output the Coder already produced, to avoid re-running the suite
            coder_test_output = self._extract_test_output(coder_result.content)

            # Tiered audit: always Security + Bug; conditionally Performance + Tests
            auditor_filter = self._select_auditor_names(coder_touched)
            logger.debug("Auto-audit: using auditors %s for %d file(s)", auditor_filter, len(coder_touched))

            # Bump soft caps so auditors get headroom after expensive coding phases
            _AUDIT_TOKEN_HEADROOM = 120_000
            _AUDIT_DOLLAR_HEADROOM = 0.50
            original_token_soft = self.tracker.budget.total_soft_cap
            original_dollar_soft = self.tracker.budget.dollar_soft_cap
            if self.tracker.is_soft_cap_reached():
                current_tokens = self.tracker.total_input + self.tracker.total_output
                self.tracker.budget.total_soft_cap = min(
                    self.tracker.budget.total_hard_cap,
                    current_tokens + _AUDIT_TOKEN_HEADROOM,
                )
                if self.tracker.budget.dollar_soft_cap > 0:
                    self.tracker.budget.dollar_soft_cap = min(
                        self.tracker.budget.dollar_hard_cap,
                        self.tracker.total_cost + _AUDIT_DOLLAR_HEADROOM,
                    )
                logger.debug(
                    "Extended soft caps for auto-audit: tokens %d→%d, dollars %.2f→%.2f",
                    original_token_soft, self.tracker.budget.total_soft_cap,
                    original_dollar_soft, self.tracker.budget.dollar_soft_cap,
                )

            audit_report = await self._run_audit(
                target_path=self._common_parent(coder_touched),
                explorer_context=delta_context,
                auditor_filter=auditor_filter,
                coder_test_output=coder_test_output,
                specific_files=coder_touched,
            )

            # Restore original soft caps
            self.tracker.budget.total_soft_cap = original_token_soft
            self.tracker.budget.dollar_soft_cap = original_dollar_soft
        else:
            audit_report = "**Auto-audit skipped** — Coder made no file changes."

        # Diff stat scoped to only the files the Coder touched
        diff_stat = await self._get_diff_stat_for_files(coder_touched) if coder_touched else ""
        diff_section = (
            f"\n\n---\n\n**Changes:**\n```\n{diff_stat}\n```"
            if diff_stat else ""
        )

        # Check for CRITICAL findings — if any, attempt auto-fix
        if "CRITICAL" in audit_report:
            await self._status("Critical findings detected — attempting auto-fix…")
            fix_result = await self.coder.run(
                f"Fix all CRITICAL issues from this audit report:\n\n{audit_report}",
                context=coder_result.content,
            )
            result = (
                f"{coder_result.content}"
                f"{diff_section}\n\n"
                f"---\n\n"
                f"**Auto-fix applied for critical findings:**\n{fix_result.content}\n\n"
                f"---\n\n"
                f"**Audit report:**\n{audit_report}"
            )
            _summary = self.tracker.get_summary()
            await self._emit("orchestrator", "agent_finished",
                             content=result, tokens_in=0, tokens_out=0,
                             iterations=0, summary=_summary)
            await self._emit("orchestrator", "task_complete", result_markdown=result, diff_stat=diff_stat,
                             summary=_summary)
            return result

        result = (
            f"{coder_result.content}"
            f"{diff_section}\n\n"
            f"---\n\n"
            f"**Auto-audit:**\n{audit_report}"
        )
        _summary = self.tracker.get_summary()
        await self._emit("orchestrator", "agent_finished",
                         content=result, tokens_in=0, tokens_out=0,
                         iterations=0, summary=_summary)
        await self._emit("orchestrator", "task_complete", result_markdown=result, diff_stat=diff_stat,
                         summary=_summary)
        return result

    # ------------------------------------------------------------------ #
    #  Report formatting                                                    #
    # ------------------------------------------------------------------ #

    async def fix_finding(self, finding_id: int) -> str:
        """
        Fix a specific finding by its number from the last audit report.
        finding_id is 1-based (as shown in the report).
        """
        if not self._last_findings:
            return "No audit findings in memory. Run `audit <path>` first."

        idx = finding_id - 1
        if idx < 0 or idx >= len(self._last_findings):
            return (
                f"Finding #{finding_id} does not exist. "
                f"Last audit had {len(self._last_findings)} finding(s)."
            )

        f = self._last_findings[idx]
        task = (
            f"Fix this issue found by the {f['source']} auditor:\n\n"
            f"**{f['title']}** [{f['severity']}]\n"
            f"File: {f.get('file', 'unknown')}"
            + (f":{f['line']}" if f.get("line") else "")
            + f"\n\nDescription: {f.get('description', '')}\n"
            f"Suggested fix: {f.get('suggestion', '')}"
        )

        await self._status(f"Fixing finding #{finding_id}: {f['title']}…")
        result = await self.coder.run(task)
        return result.content

    # ------------------------------------------------------------------ #
    #  Auto-audit helpers                                                  #
    # ------------------------------------------------------------------ #

    async def _build_delta_context(self, files: list[str], max_chars: int = 30_000) -> str:
        """Build a focused delta context showing exactly what the Coder changed.

        Uses `git diff` for tracked files and direct file reads for new/untracked files.
        Total output is capped at max_chars to avoid bloating auditor context windows.
        """
        if not files:
            return ""

        rc, known_out, _ = await _git("git", "ls-files", "--", *files)
        known_set = set(known_out.strip().splitlines()) if rc == 0 else set()
        tracked = [f for f in files if f in known_set]
        untracked = [f for f in files if f not in known_set]

        parts: list[str] = []
        total = 0

        if tracked:
            rc, diff_out, _ = await _git("git", "diff", "HEAD", "--", *tracked)
            if rc == 0 and diff_out.strip():
                snippet = diff_out[:max_chars]
                parts.append(f"## Git diff (Coder changes)\n```diff\n{snippet}\n```")
                total += len(snippet)

        for f in untracked:
            if total >= max_chars:
                break
            fp = Path(f)
            if fp.is_file():
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    snippet = text[:max_chars - total]
                    parts.append(f"## New file: {f}\n```\n{snippet}\n```")
                    total += len(snippet)
                except OSError:
                    pass

        if not parts:
            return ""

        header = f"Files changed by Coder ({len(files)}): {', '.join(files[:10])}"
        if len(files) > 10:
            header += f" (+{len(files) - 10} more)"
        return header + "\n\n" + "\n\n".join(parts)

    @staticmethod
    def _select_auditor_names(touched_files: list[str]) -> list[str]:
        """Choose which auditors to run based on touched file types.

        Security and Bug always run. Performance runs for multi-file source changes.
        Test runs when source files or test files were modified.
        """
        _SOURCE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"}
        _TEST_PATTERNS = ("test_", "_test.", "tests/", "spec/", ".spec.", ".test.")

        has_source = any(Path(f).suffix in _SOURCE_EXTS for f in touched_files)
        has_test = any(any(pat in f for pat in _TEST_PATTERNS) for f in touched_files)

        selected = ["security_auditor", "bug_auditor"]
        if has_source and len(touched_files) > 1:
            selected.append("performance_auditor")
        if has_source or has_test:
            selected.append("test_auditor")
        return selected

    @staticmethod
    def _extract_test_output(coder_content: str) -> str:
        """Extract pytest/unittest output from Coder result, if present.

        Returns a capped snippet so the Test auditor can skip redundant re-runs.
        """
        import re as _re
        # Look for pytest session block or pass/fail summary lines
        patterns = [
            r"(={3,}.*?test session starts[\s\S]{0,3000}(?:\d+ passed|\d+ failed|\d+ error)[^\n]*)",
            r"((?:PASSED|FAILED|ERROR)[^\n]*\n(?:[\s\S]{0,2000})?(?:\d+ passed|\d+ failed|\d+ error)[^\n]*)",
        ]
        for pattern in patterns:
            m = _re.search(pattern, coder_content, _re.IGNORECASE)
            if m:
                return m.group(1).strip()[:1500]
        return ""

    # ------------------------------------------------------------------ #
    #  Git helpers                                                         #
    # ------------------------------------------------------------------ #

    async def _get_dirty_files(self) -> list[str]:
        """Return all currently dirty files: tracked modifications + untracked new files."""
        files: list[str] = []

        # Modified tracked files (staged or unstaged relative to last commit)
        rc, out, _ = await _git("git", "diff", "--name-only", "HEAD")
        if rc == 0 and out.strip():
            files.extend(f.strip() for f in out.strip().splitlines() if f.strip())

        # Brand-new files not yet added to the index
        rc2, out2, _ = await _git("git", "ls-files", "--others", "--exclude-standard")
        if rc2 == 0 and out2.strip():
            files.extend(f.strip() for f in out2.strip().splitlines() if f.strip())

        return files

    @staticmethod
    def _common_parent(paths: list[str]) -> str:
        """Return the deepest common directory of a list of file paths."""
        if not paths:
            return "."
        if len(paths) == 1:
            parent = Path(paths[0]).parent
            return str(parent) if str(parent) != "." else "."
        parents = [Path(p).parent for p in paths]
        common = parents[0]
        for p in parents[1:]:
            while common != p and common != Path("."):
                try:
                    p.relative_to(common)
                    break
                except ValueError:
                    common = common.parent
        return str(common) if str(common) != "." else "."

    async def _get_diff_stat_for_files(self, files: list[str]) -> str:
        """Return a compact git diff --stat scoped to the given files only."""
        if not files:
            return ""
        # Single git call to find which files are already tracked — O(1) instead of O(n)
        rc, known_out, _ = await _git("git", "ls-files", "--", *files)
        known_set = set(known_out.strip().splitlines()) if rc == 0 else set()
        tracked = [f for f in files if f in known_set]
        untracked = [f for f in files if f not in known_set]

        parts: list[str] = []
        if tracked:
            rc, out, _ = await _git("git", "diff", "--stat", "HEAD", "--", *tracked)
            if rc == 0 and out.strip():
                parts.append(out.strip())
        for uf in untracked:
            parts.append(f"{uf}  (new file)")

        return "\n".join(parts)

    @staticmethod
    def _extract_path_hints(task: str) -> list[str]:
        """Extract file/directory path-like tokens from a task description."""
        # Match tokens that look like paths: contain a slash, dot-extension, or end with /
        raw = re.findall(r"[\w./\\-]+\.[\w]+|[\w./\\-]+/", task)
        # Filter out noise (single dots, URLs, etc.)
        hints = [
            p.strip("/\\") for p in raw
            if len(p) > 2 and not p.startswith("http") and ("." in p or "/" in p)
        ]
        return list(dict.fromkeys(hints))  # deduplicate preserving order

    async def _read_files_as_context(self, target: str, max_chars: int = 30_000) -> str:
        """Read source files under target directly, returning concatenated content.

        Used as an Explorer fallback when the Explorer agent hits its token cap
        before producing a summary.
        """
        source_exts = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"}
        target_path = Path(target)
        collected: list[str] = []
        total = 0

        if target_path.is_file():
            candidates = [target_path]
        else:
            candidates = sorted(
                (p for p in target_path.rglob("*") if p.suffix in source_exts and p.is_file()),
                key=lambda p: p.stat().st_size,
            )

        for p in candidates:
            if total >= max_chars:
                break
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            snippet = text[:max_chars - total]
            collected.append(f"### {p}\n```\n{snippet}\n```")
            total += len(snippet)

        return "\n\n".join(collected)

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

        # Save for `fix <id>` command
        self._last_findings = findings

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
    Extract structured findings from auditor output.

    Tries JSON code block first (```json {...} ```), falls back to
    legacy FINDING: block regex parsing for backward compatibility.
    """
    import json as _json

    # Attempt 1: extract a ```json ... ``` code block
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        try:
            data = _json.loads(json_match.group(1))
            raw_findings = data.get("findings", [])
            findings = []
            for item in raw_findings:
                if not isinstance(item, dict):
                    continue
                severity = str(item.get("severity", "LOW")).upper()
                if severity not in _SEVERITY_RANK:
                    severity = "LOW"
                findings.append({
                    "title": str(item.get("title", "")),
                    "severity": severity,
                    "file": str(item.get("file", "")),
                    "line": str(item.get("line", "")),
                    "description": str(item.get("description", "")),
                    "suggestion": str(item.get("suggestion", "")),
                    "source": source,
                })
            return findings
        except (_json.JSONDecodeError, AttributeError):
            pass  # fall through to regex

    # Attempt 2: legacy FINDING: block regex (backward compat)
    findings = []
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
