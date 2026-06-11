"""
Base Agent Loop — ReAct pattern.

Think → Act (tool call) → Observe (tool result) → repeat or yield.

All agents (explorer, coder, auditors) inherit from this base class.
Each subclass defines its own system prompt, model, and allowed tools.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import anthropic

from src.core.llm_client import LLMClient
from src.core.token_tracker import TokenTracker
from src.core.context_manager import ContextManager
from src.tools.registry import ToolRegistry
from src.config import ArgusConfig

if TYPE_CHECKING:
    from src.gui.event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Final result returned by an agent run."""
    content: str
    agent_name: str
    iterations: int
    total_input_tokens: int = 0
    total_output_tokens: int = 0


@dataclass
class AgentDefinition:
    name: str
    system_prompt: str
    model_key: str  # attribute name on config.models (e.g. "coder", "explorer")
    max_tokens: int
    tool_names: list


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
        event_bus: "Optional[EventBus]" = None,
    ):
        self.config = config
        self.llm = llm_client
        self.tracker = token_tracker
        self.tools = tool_registry
        self._event_bus = event_bus
        self._compact_requested = asyncio.Event()
        self.context = ContextManager(config.context, emit_fn=self._emit_sync_compaction)
        self._last_messages: list[dict] = []  # snapshot of last run's final history

    def request_compact(self) -> None:
        """Set the flag so the next live run trims aggressively."""
        self._compact_requested.set()

    def get_history_preview(self) -> tuple[str, str, int]:
        """
        Trim _last_messages and return (before_text, after_text, dropped).
        Does NOT emit any events — caller handles that.
        Returns ("", "", 0) if no history is available.
        """
        if not self._last_messages:
            return "", "", 0

        def _preview(msg: dict) -> str:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        for inner in (block.get("content") or []):
                            if isinstance(inner, dict) and inner.get("type") == "text":
                                return f"[{role}] tool_result: {inner['text'][:120]}"
                return f"[{role}] (tool result)"
            return f"[{role}] {str(content)[:120]}"

        before_msgs = list(self._last_messages)
        # Use context manager's trim logic but suppress its emit by temporarily removing emit_fn
        orig_emit = self.context._emit_fn
        self.context._emit_fn = None
        try:
            trimmed = self.context.trim_history(
                list(before_msgs),
                max_tokens=self.config.context.max_history_tokens // 2,
            )
        finally:
            self.context._emit_fn = orig_emit
        self._last_messages = trimmed

        dropped = len(before_msgs) - len(trimmed)
        before_text = "\n".join(_preview(m) for m in before_msgs)
        after_text  = "\n".join(_preview(m) for m in trimmed)
        return before_text[:2000], after_text[:1000], dropped

    def _emit_sync_compaction(self, event_type: str, **data) -> None:
        """Sync bridge: fires compaction event onto EventBus without awaiting."""
        if self._event_bus is not None:
            self._event_bus.emit_sync(self.name, event_type, **data)

    async def _emit(self, event_type: str, **data) -> None:
        """Emit a GUI event if an event bus is wired in; no-op otherwise."""
        if self._event_bus is not None:
            await self._event_bus.emit(self.name, event_type, **data)

    def get_model(self) -> str:
        if hasattr(self, "_defn"):
            return getattr(self.config.models, self._defn.model_key)
        return self.config.models.orchestrator

    def get_max_tokens(self) -> int:
        if hasattr(self, "_defn"):
            return self._defn.max_tokens
        return 4096

    def get_tool_names(self) -> list[str]:
        if hasattr(self, "_defn"):
            return list(self._defn.tool_names)
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
        # Compact injected context if it exceeds the allowed budget fraction
        if context:
            context = await self.context.compact_injected_context(
                context, self.llm,
                token_tracker=self.tracker,
                agent_name=self.name,
            )
            initial_content = f"Context:\n{context}\n\nTask: {task}"
        else:
            initial_content = task

        messages: list[dict] = [{"role": "user", "content": initial_content}]
        tool_schemas = self.tools.get_schemas(self.get_tool_names())
        model = self.get_model()
        total_in = 0
        total_out = 0
        # Last substantive text the agent produced — returned on budget-cap paths
        # so callers get partial work instead of a bare bracket error string.
        last_content: str = ""

        logger.debug(
            "[%s] Starting run: model=%s, tools=%s",
            self.name, model, self.get_tool_names()
        )
        await self._emit(
            "agent_started",
            task_preview=task[:120],
            model=model,
            tools=self.get_tool_names(),
        )

        _winding_down = False       # set True on soft-cap hit; hard-stop after 2 more iters
        _soft_iters_left = 2        # how many more iterations after soft cap

        _max_iters = self.config.agent.max_iterations_per_agent.get(
            self.name, self.config.agent.max_iterations
        )

        def _partial_or_bracket(bracket_msg: str) -> str:
            if last_content:
                return last_content + f"\n\n[Note: {self.name} stopped early — {bracket_msg.strip('[]')}]"
            return bracket_msg

        async def _finish(content: str, tokens_in: int, tokens_out: int, iterations: int) -> AgentResult:
            """Emit agent_finished + live token_update, then return AgentResult."""
            self._last_messages = list(messages)  # snapshot for compact before/after
            await self._emit("agent_finished", content=content, tokens_in=tokens_in,
                             tokens_out=tokens_out, iterations=iterations)
            if self._event_bus is not None:
                await self._event_bus.emit(self.name, "token_update",
                                           summary=self.tracker.get_summary())
            return AgentResult(
                content=content,
                agent_name=self.name,
                iterations=iterations,
                total_input_tokens=tokens_in,
                total_output_tokens=tokens_out,
            )

        for iteration in range(1, _max_iters + 1):
            await self._emit(
                "agent_iteration",
                iteration=iteration,
                max_iterations=_max_iters,
            )

            # --- Budget checks ---
            if self.tracker.is_hard_cap_reached():
                logger.warning("[%s] Hard cap reached", self.name)
                await self._emit("budget_exceeded", reason="hard_cap")
                _content = _partial_or_bracket("[Budget hard limit reached — agent stopped]")
                return await _finish(_content, total_in, total_out, iteration)

            if _winding_down:
                _soft_iters_left -= 1
                if _soft_iters_left <= 0:
                    logger.warning("[%s] Soft cap escalated to hard stop after wind-down", self.name)
                    await self._emit("budget_exceeded", reason="soft_cap")
                    _content = _partial_or_bracket("[Budget soft limit exceeded — agent wound down]")
                    return await _finish(_content, total_in, total_out, iteration)

            if self.tracker.is_agent_cap_reached(self.name):
                logger.warning("[%s] Per-agent token cap reached", self.name)
                await self._emit("budget_exceeded", reason="per_agent_cap")
                _content = _partial_or_bracket(f"[Per-agent budget exceeded for {self.name}]")
                return await _finish(_content, total_in, total_out, iteration)

            # --- LLM call ---
            try:
                response = await self.llm.chat(
                    model=model,
                    system=self.system_prompt,
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    max_tokens=self.get_max_tokens(),
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
            self.tracker.add(
                self.name, response.input_tokens, response.output_tokens, model,
                cache_creation_tokens=response.cache_creation_tokens,
                cache_read_tokens=response.cache_read_tokens,
            )
            total_in += response.input_tokens
            total_out += response.output_tokens

            logger.debug(
                "[%s] iter=%d stop=%s tool_calls=%d in=%d out=%d",
                self.name, iteration, response.stop_reason,
                len(response.tool_calls), response.input_tokens, response.output_tokens
            )

            # Track last substantive content for budget-cap fallback
            if response.content:
                last_content = response.content
                await self._emit("agent_text", content=response.content)

            # --- End of turn: return final answer ---
            # Also catch max_tokens or any stop_reason with no tool calls
            if response.stop_reason == "end_turn" or not response.tool_calls:
                content = response.content or "[No response]"
                logger.debug("[%s] Finished in %d iterations", self.name, iteration)
                return await _finish(content, total_in, total_out, iteration)

            # --- Execute tool calls ---
            # Append assistant message ONCE with the raw content blocks
            messages.append({"role": "assistant", "content": response.raw_content})

            # Execute all tool calls concurrently and collect results into a SINGLE user message
            async def _execute_one(tool_call: dict) -> dict:
                logger.debug("[%s] Calling tool: %s", self.name, tool_call["name"])
                input_preview = str(tool_call["input"])[:200]
                await self._emit(
                    "tool_call",
                    tool_name=tool_call["name"],
                    input_preview=input_preview,
                )
                _t0 = time.monotonic()
                result = await self.tools.execute(tool_call["name"], tool_call["input"])
                _duration_ms = int((time.monotonic() - _t0) * 1000)
                await self._emit(
                    "tool_result",
                    tool_name=tool_call["name"],
                    result_preview=result[:300],
                    is_error=result.startswith(("Error:", "BLOCKED:", "DENIED:")),
                    duration_ms=_duration_ms,
                )

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

                return tool_result_block

            tool_results = list(
                await asyncio.gather(*[_execute_one(tc) for tc in response.tool_calls])
            )

            # Soft cap: append wind-down notice as a text block IN the same user message
            # (cannot add a separate user message — Anthropic requires alternating turns)
            if not _winding_down and self.tracker.is_soft_cap_reached():
                logger.warning("[%s] Soft cap reached — agent will wind down", self.name)
                _winding_down = True
                tool_results.append({
                    "type": "text",
                    "text": (
                        "⚠️ Budget soft limit reached. "
                        "Wrap up your current work immediately and return your final response.\n\n"
                        "At the END of your response, include this exact block:\n"
                        "HANDOFF:\n"
                        "completed: <one sentence: what you finished>\n"
                        "remaining: <one sentence: what you did not complete>\n"
                        "context_for_next: <key facts a fresh agent needs to continue>\n\n"
                        "Do not start any new tasks or tool calls."
                    ),
                })

            # All tool results for this turn go into ONE user message
            messages.append({"role": "user", "content": tool_results})

            # Trim history if approaching context limit
            messages = self.context.trim_history(messages)

            # Manual compact requested: force aggressive trim at half budget
            if self._compact_requested.is_set():
                self._compact_requested.clear()
                messages = self.context.trim_history(
                    messages,
                    max_tokens=self.config.context.max_history_tokens // 2,
                )

        # Max iterations exhausted
        logger.warning("[%s] Max iterations (%d) reached", self.name, _max_iters)
        await self._emit("budget_exceeded", reason="max_iterations")
        _content = _partial_or_bracket(
            f"[Max iterations ({_max_iters}) reached — agent stopped]"
        )
        return await _finish(_content, total_in, total_out, _max_iters)


def make_agent(
    defn: AgentDefinition,
    config,
    llm_client: LLMClient,
    token_tracker: TokenTracker,
    tool_registry: ToolRegistry,
    event_bus=None,
) -> BaseAgent:
    """Instantiate a BaseAgent from an AgentDefinition (no subclassing needed)."""
    agent = BaseAgent(config, llm_client, token_tracker, tool_registry, event_bus=event_bus)
    agent.name = defn.name
    agent.system_prompt = defn.system_prompt
    agent._defn = defn
    return agent
