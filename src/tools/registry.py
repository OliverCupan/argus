"""
Tool Registry — central hub for all agent tools.

Each tool registers with:
- name: unique identifier
- description: what it does (sent to LLM)
- input_schema: JSON schema for parameters
- handler: async function that executes the tool

The registry generates Anthropic-compatible tool definitions
and dispatches tool calls to the correct handler.
"""

import inspect
import logging
from dataclasses import dataclass, field
from typing import Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        """Register a tool."""
        self._tools[tool.name] = tool

    def get_schemas(self, tool_names: list[str] | None = None) -> list[dict]:
        """
        Get Anthropic-compatible tool definitions.

        Args:
            tool_names: List of tool names to include. None = all tools.

        Returns:
            List of tool definition dicts for the API call.
        """

        tools = self._tools.values()
        if tool_names:
            tools = [t for t in tools if t.name in tool_names]

        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    async def execute(self, name: str, inputs: dict) -> str:
        """
        Execute a tool by name with given inputs.

        Args:
            name: Tool name
            inputs: Tool input parameters

        Returns:
            Tool result as string
        """

        if name not in self._tools:
            return f"Error: Unknown tool '{name}'"

        tool = self._tools[name]
        try:
            # Filter to only known parameters — prevents TypeError from
            # extra kwargs the LLM sometimes sends
            sig = inspect.signature(tool.handler)
            valid_params = set(sig.parameters.keys())
            filtered = {k: v for k, v in inputs.items() if k in valid_params}
            if filtered != inputs:
                dropped = set(inputs) - set(filtered)
                logger.debug("Tool %s: dropped unknown params %s", name, dropped)

            result = await tool.handler(**filtered)
            logger.debug("Tool %s executed OK", name)
            return str(result)
        except Exception as e:
            logger.warning("Tool %s raised: %s", name, e)
            return f"Error executing {name}: {e}"


def build_registry(config, confirm_callback=None, llm=None, tracker=None, event_bus=None) -> ToolRegistry:
    """
    Create and populate the tool registry with all available tools.

    Args:
        config: ArgusConfig instance
        confirm_callback: Optional async callable(command: str) -> bool
            Passed to bash_tool for REVIEW-level command approval.
        llm: Optional LLMClient — required to register dispatch_agents.
        tracker: Optional TokenTracker — required to register dispatch_agents.
        event_bus: Optional EventBus forwarded to dispatched sub-agents.
    """
    from src.tools.bash_tool import create_bash_tool
    from src.tools.file_reader import create_file_reader_tool
    from src.tools.file_editor import create_file_editor_tool
    from src.tools.file_writer import create_file_writer_tool

    registry = ToolRegistry()

    registry.register(create_bash_tool(config, confirm_callback=confirm_callback))
    registry.register(create_file_reader_tool())
    registry.register(create_file_editor_tool(config=config))
    registry.register(create_file_writer_tool(config=config))

    # Register dispatch_agents only when LLM + tracker are provided
    if llm is not None and tracker is not None:
        from src.tools.agent_dispatch import create_agent_dispatch_tool
        registry.register(create_agent_dispatch_tool(config, llm, tracker, registry, event_bus=event_bus))

    return registry
