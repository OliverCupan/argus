"""
Context Manager — protects the context window.

Two mechanisms:
1. Tool output compaction: long outputs get summarized via Haiku before
   being added to agent history.
2. Sliding window: oldest messages get dropped when history grows too large,
   always dropping in pairs (assistant + tool_result) to keep the
   conversation structurally valid.
"""

import json
import logging

from src.config import ContextConfig

logger = logging.getLogger(__name__)

# Hard cap before sending to Haiku for summarization (~100K tokens)
_MAX_CHARS_BEFORE_COMPACTION = 400_000


class ContextManager:
    def __init__(self, config: ContextConfig):
        self.config = config

    async def maybe_compact(
        self,
        tool_output: str,
        llm_client,
        token_tracker=None,
        agent_name: str = "",
    ) -> str:
        """
        If tool_output exceeds compaction_threshold tokens, summarize via Haiku.
        Tracks compaction API cost against token_tracker if provided.

        Returns original or compacted output.
        """
        # Hard truncation before even estimating — guard against huge outputs
        if len(tool_output) > _MAX_CHARS_BEFORE_COMPACTION:
            original_chars = len(tool_output)
            tool_output = tool_output[:_MAX_CHARS_BEFORE_COMPACTION]
            tool_output += f"\n\n[Output truncated: {original_chars} chars → {_MAX_CHARS_BEFORE_COMPACTION} chars before summarization]"
            logger.debug("Hard-truncated tool output from %d chars", original_chars)

        estimated = self.estimate_tokens(tool_output)
        if estimated <= self.config.compaction_threshold:
            return tool_output

        logger.debug(
            "Compacting tool output: ~%d tokens → Haiku summarization", estimated
        )
        response = await llm_client.chat(
            model=self.config.compaction_model,
            system="You are a concise technical summarizer.",
            messages=[{
                "role": "user",
                "content": (
                    "Summarize this tool output concisely, preserving all key information "
                    "including file paths, function names, error messages, line numbers, "
                    "and important values:\n\n" + tool_output
                ),
            }],
            max_tokens=1024,
        )

        if token_tracker and agent_name:
            token_tracker.add(
                f"{agent_name}/_compaction",
                response.input_tokens,
                response.output_tokens,
                self.config.compaction_model,
            )

        return f"[Compacted]\n{response.content}"

    def trim_history(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> list[dict]:
        """
        Trim conversation history to fit within token budget.

        Keeps the first message (original task) and the most recent messages.
        Always drops in pairs (assistant turn + user/tool_result turn) to keep
        the conversation structurally valid for the Anthropic API.
        """
        limit = max_tokens or self.config.max_history_tokens
        total = sum(self._estimate_message_tokens(m) for m in messages)

        if total <= limit:
            return messages

        if len(messages) < 2:
            return messages

        first_msg = messages[0]
        remaining = list(messages[1:])

        # Drop in pairs to preserve assistant↔tool_result integrity
        while len(remaining) >= 2 and total > limit:
            dropped_a = remaining.pop(0)
            dropped_b = remaining.pop(0)
            total -= self._estimate_message_tokens(dropped_a)
            total -= self._estimate_message_tokens(dropped_b)
            logger.debug("Trimmed 2 messages from history, ~%d tokens remaining", total)

        trimmed_note: dict = {
            "role": "user",
            "content": "[Earlier conversation context was trimmed to fit within the context window]",
        }
        result = [first_msg, trimmed_note] + remaining
        logger.debug(
            "History trimmed: %d → %d messages", len(messages), len(result)
        )
        return result

    def _estimate_message_tokens(self, message: dict) -> int:
        """Estimate token count for a single message (any content shape)."""
        content = message.get("content", "")
        if isinstance(content, str):
            return self.estimate_tokens(content)
        if isinstance(content, list):
            # List of content blocks or tool_result dicts
            return self.estimate_tokens(json.dumps(content))
        return 0

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token for English."""
        return max(1, len(text) // 4)
