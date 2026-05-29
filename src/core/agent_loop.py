"""
Base Agent Loop — ReAct pattern.

Think → Act (tool call) → Observe (tool result) → repeat or yield.

All agents (explorer, coder, auditors) inherit from this base class.
Each subclass defines its own system prompt, model, and allowed tools.
"""

import logging
from dataclasses import dataclass

import anthropic

from src.core.llm_client import LLMClient
from src.core.token_tracker import TokenTracker
from src.core.context_manager import ContextManager
from src.tools.registry import ToolRegistry
from src.config import ArgusConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Final result returned by an agent run."""
    content: str
    agent_name: str
    iterations: int
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class BaseAgent:
    """
    Base class for all Argus agents.

    Subclasses must override:
        name: str                  — identifier for tracking
        system_prompt: str         — defines the agent's persona and rules
        get_model() -> str         — which model to use
        get_tool_names() -> list   — tool names from the registry
    """

    name: str = "base_agent"
    system_prompt: str = "You are a helpful assistant."

    def __init__(
        self,
        config: ArgusConfig,
        llm_client: LLMClient,
        token_tracker: TokenTracker,
        tool_registry: ToolRegistry,
    ):
        self.config = config
        self.llm = llm_client
        self.tracker = token_tracker
        self.tools = tool_registry
        self.context = ContextManager(config.context)

    def get_model(self) -> str:
        return self.config.models.orchestrator

    def get_tool_names(self) -> list[str]:
        return []

    async def run(self, task: str, context: str = "") -> AgentResult:
        """
        Execute the ReAct loop for a given task.

        Args:
            task: Task description / user message
            context: Optional injected context (e.g. explorer summary)

        Returns:
            AgentResult with the final response and token stats
        """
        # Build initial message
        if context:
            initial_content = f"Context:\n{context}\n\nTask: {task}"
        else:
            initial_content = task

        messages: list[dict] = [{"role": "user", "content": initial_content}]
        tool_schemas = self.tools.get_schemas(self.get_tool_names())
        model = self.get_model()
        total_in = 0
        total_out = 0

        logger.debug(
            "[%s] Starting run: model=%s, tools=%s",
            self.name, model, self.get_tool_names()
        )

        for iteration in range(1, self.config.agent.max_iterations + 1):

            # --- Budget checks ---
            if self.tracker.is_hard_cap_reached():
                logger.warning("[%s] Hard token cap reached", self.name)
                return AgentResult(
                    content="[Token budget exceeded — agent stopped]",
                    agent_name=self.name,
                    iterations=iteration,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                )

            if self.tracker.is_agent_cap_reached(self.name):
                logger.warning("[%s] Per-agent token cap reached", self.name)
                return AgentResult(
                    content=f"[Per-agent budget exceeded for {self.name}]",
                    agent_name=self.name,
                    iterations=iteration,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                )

            # --- LLM call ---
            try:
                response = await self.llm.chat(
                    model=model,
                    system=self.system_prompt,
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                )
            except anthropic.APIError as e:
                logger.error("[%s] API error on iteration %d: %s", self.name, iteration, e)
                return AgentResult(
                    content=f"[API error: {e}]",
                    agent_name=self.name,
                    iterations=iteration,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                )

            # --- Track tokens ---
            self.tracker.add(self.name, response.input_tokens, response.output_tokens, model)
            total_in += response.input_tokens
            total_out += response.output_tokens

            logger.debug(
                "[%s] iter=%d stop=%s tool_calls=%d in=%d out=%d",
                self.name, iteration, response.stop_reason,
                len(response.tool_calls), response.input_tokens, response.output_tokens
            )

            # --- End of turn: return final answer ---
            # Also catch max_tokens or any stop_reason with no tool calls
            if response.stop_reason == "end_turn" or not response.tool_calls:
                content = response.content or "[No response]"
                logger.debug("[%s] Finished in %d iterations", self.name, iteration)
                return AgentResult(
                    content=content,
                    agent_name=self.name,
                    iterations=iteration,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                )

            # --- Execute tool calls ---
            # Append assistant message ONCE with the raw content blocks
            messages.append({"role": "assistant", "content": response.raw_content})

            # Execute all tool calls and collect results into a SINGLE user message
            tool_results: list[dict] = []
            for tool_call in response.tool_calls:
                logger.debug("[%s] Calling tool: %s", self.name, tool_call["name"])
                result = await self.tools.execute(tool_call["name"], tool_call["input"])

                # Compact if too long
                result = await self.context.maybe_compact(
                    result,
                    self.llm,
                    token_tracker=self.tracker,
                    agent_name=self.name,
                )

                tool_result_block: dict = {
                    "type": "tool_result",
                    "tool_use_id": tool_call["id"],
                    "content": result,
                }
                # Signal failures to the model so it can reason about them
                if result.startswith(("Error:", "BLOCKED:", "DENIED:")):
                    tool_result_block["is_error"] = True

                tool_results.append(tool_result_block)

            # All tool results for this turn go into ONE user message
            messages.append({"role": "user", "content": tool_results})

            # Trim history if approaching context limit
            messages = self.context.trim_history(messages)

        # Max iterations exhausted
        logger.warning("[%s] Max iterations (%d) reached", self.name, self.config.agent.max_iterations)
        return AgentResult(
            content=f"[Max iterations ({self.config.agent.max_iterations}) reached — agent stopped]",
            agent_name=self.name,
            iterations=self.config.agent.max_iterations,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
        )
