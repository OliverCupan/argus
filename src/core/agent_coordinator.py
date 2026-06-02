"""
Agent Coordinator — tracks running agents and enforces collaboration rules.

COLLABORATION RULES:
1. Only ONE agent writes files at a time (Coder in coding mode).
2. Auditors are READ-ONLY — they can read files and run bash but never edit.
3. Explorer is READ-ONLY.
4. Challenger is READ-ONLY (reads to verify assumptions).
5. When Coder needs a file being read by an auditor, Coder waits
   (auditors are fast; Coder is slow — minimal wait in practice).
6. Parallel auditors share read access freely (no write conflicts).
7. Auto-audit runs AFTER Coder is fully done (no overlap by design).

This module provides the FileLockManager singleton and an AgentCoordinator
that tracks which agents are currently active.
"""

import asyncio
import logging
from typing import Optional

from src.core.file_lock import FileLockManager

logger = logging.getLogger(__name__)

# Agents that are permitted to write files
_WRITE_AGENTS = frozenset({"coder"})

# Agents that are strictly read-only
_READ_ONLY_AGENTS = frozenset({
    "explorer", "challenger",
    "security_auditor", "bug_auditor", "performance_auditor", "test_auditor",
})


class AgentCoordinator:
    """
    Central coordinator for multi-agent file access.

    Maintains:
    - A shared FileLockManager (process-wide singleton)
    - A registry of which agents are currently running
    - Per-agent file access history for the current session
    """

    def __init__(self):
        self.lock_manager = FileLockManager()
        self._running: dict[str, asyncio.Task] = {}
        self._file_access_log: dict[str, list[tuple[str, str]]] = {}  # file → [(agent, mode)]

    def is_running(self, agent_name: str) -> bool:
        return agent_name in self._running and not self._running[agent_name].done()

    def running_agents(self) -> list[str]:
        return [name for name, task in self._running.items() if not task.done()]

    def register_task(self, agent_name: str, task: asyncio.Task) -> None:
        self._running[agent_name] = task

    def log_access(self, agent_name: str, path: str, mode: str) -> None:
        """Record that an agent accessed a file (for auditing/debugging)."""
        if path not in self._file_access_log:
            self._file_access_log[path] = []
        self._file_access_log[path].append((agent_name, mode))

    def can_write(self, agent_name: str) -> bool:
        """Returns True if this agent is permitted to write files."""
        return agent_name.lower() in _WRITE_AGENTS

    def status(self) -> dict:
        return {
            "running": self.running_agents(),
            "locks": self.lock_manager.status(),
        }


# Module-level singleton — shared by all agents in a session
_coordinator: Optional[AgentCoordinator] = None


def get_coordinator() -> AgentCoordinator:
    """Get or create the global AgentCoordinator instance."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AgentCoordinator()
    return _coordinator


def reset_coordinator() -> None:
    """Reset the singleton (for testing)."""
    global _coordinator
    _coordinator = None
