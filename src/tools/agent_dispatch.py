"""
Agent Dispatch Tool — lets the Coder spawn parallel sub-agents mid-task.

Agents are drawn from the fixed pool. The Coder itself is excluded to
prevent recursive full-write agents. Each spawned agent uses its own
per-agent token budget slice.
"""

import asyncio
import logging

from src.tools.registry import Tool
from src.core.agent_loop import make_agent

logger = logging.getLogger(__name__)


def _build_pool():
    """Import definitions lazily to avoid circular imports at module load."""
    from src.agents.definitions import (
        EXPLORER_DEF, CHALLENGER_DEF,
        SECURITY_AUDITOR_DEF, BUG_AUDITOR_DEF,
        PERFORMANCE_AUDITOR_DEF, TEST_AUDITOR_DEF,
        WORKER_DEF,
    )
    return {
        "explorer": EXPLORER_DEF,
        "challenger": CHALLENGER_DEF,
        "security_auditor": SECURITY_AUDITOR_DEF,
        "bug_auditor": BUG_AUDITOR_DEF,
        "performance_auditor": PERFORMANCE_AUDITOR_DEF,
        "test_auditor": TEST_AUDITOR_DEF,
        "worker": WORKER_DEF,
        # "coder" intentionally excluded — no recursive full-write agents
    }


def create_agent_dispatch_tool(config, llm, tracker, tools, event_bus=None) -> Tool:
    """Factory: returns the dispatch_agents tool, closing over shared resources."""
    max_workers = getattr(getattr(config, "agent", None), "max_dispatch_workers", 5)

    async def handler(tasks: list) -> str:
        if not tasks:
            return "Error: no tasks provided"
        if len(tasks) > max_workers:
            return f"Error: {len(tasks)} tasks exceeds max_dispatch_workers ({max_workers})"

        pool = _build_pool()
        valid_names = list(pool.keys())
        lines = []
        coroutines = []

        for spec in tasks:
            if not isinstance(spec, dict):
                lines.append("Error: each task must be an object with 'agent' and 'task' keys")
                continue
            agent_name = spec.get("agent", "worker")
            task_text  = spec.get("task", "").strip()

            if not task_text:
                lines.append(f"[{agent_name}]: Error: empty task")
                continue
            if agent_name not in pool:
                lines.append(
                    f"[{agent_name}]: Error: unknown agent. "
                    f"Valid names: {valid_names}"
                )
                continue

            defn  = pool[agent_name]
            agent = make_agent(defn, config, llm, tracker, tools, event_bus=event_bus)
            coroutines.append((agent_name, agent, task_text))

        if not coroutines:
            return "\n".join(lines) if lines else "Error: no valid tasks"

        async def run_one(name: str, agent, task: str) -> str:
            try:
                result = await agent.run(task)
                return f"[{name}]:\n{result.content}"
            except Exception as exc:
                logger.error("dispatch_agents: agent %s failed: %s", name, exc)
                return f"[{name}]: Error — {exc}"

        parallel_results = await asyncio.gather(
            *[run_one(n, a, t) for n, a, t in coroutines],
            return_exceptions=False,
        )
        lines.extend(parallel_results)
        return "\n\n".join(lines)

    valid_agent_names = list(_build_pool().keys())

    return Tool(
        name="dispatch_agents",
        description=(
            "Spawn multiple sub-agents to run independent tasks in parallel. "
            "Each agent runs its own ReAct loop and returns its result. "
            f"Max {max_workers} agents per call. "
            "Use when tasks can be split into independent parallel workstreams — "
            "e.g. analyse three separate modules simultaneously."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": f"Tasks to run in parallel (max {max_workers})",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "enum": valid_agent_names,
                                "description": "Agent to use for this task",
                            },
                            "task": {
                                "type": "string",
                                "description": "Task description for this agent",
                            },
                        },
                        "required": ["agent", "task"],
                    },
                    "minItems": 1,
                    "maxItems": max_workers,
                }
            },
            "required": ["tasks"],
        },
        handler=handler,
    )
