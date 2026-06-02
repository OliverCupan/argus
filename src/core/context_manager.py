"""
Context Manager — protects the context window.

Three mechanisms:
1. Tiered tool output compaction: outputs are handled based on size:
   - Tier 1 (< threshold tokens):  pass through unchanged
   - Tier 2 (threshold - 5K tokens): extract key facts only (structured prompt)
   - Tier 3 (5K - 20K tokens):     full Haiku summarization
   - Tier 4 (> 20K tokens):        hard truncate to 20K chars, then Tier 3

2. Smart history trimming: importance-scored message retention.
   Always keeps: first message (task), last assistant message, messages
   containing file edits. Drops low-importance messages first.

3. Cross-agent context injection budget: if injected context exceeds
   max_context_injection_pct of max_history_tokens, compact it first.
"""

import json
import logging
from typing import Optional

from src.config import ContextConfig

logger = logging.getLogger(__name__)

# Tier thresholds (in tokens)
_TIER2_THRESHOLD = 5_000    # Tier 2: key-facts extraction
_TIER3_THRESHOLD = 20_000   # Tier 3: full summarization
_TIER4_CHARS = 80_000       # Hard char truncation before Tier 3 (≈ 20K tokens)

# Hard char cap before any compaction attempt
_MAX_CHARS_BEFORE_COMPACTION = 400_000


class ContextManager:
    def __init__(self, config: ContextConfig):
        self.config = config

    # ------------------------------------------------------------------ #
    #  Tool output compaction                                              #
    # ------------------------------------------------------------------ #

    async def maybe_compact(
        self,
        tool_output: str,
        llm_client,
        token_tracker=None,
        agent_name: str = "",
    ) -> str:
        """
        Route tool output through the appropriate compaction tier.
        Returns the original or compacted output.
        """
        # Hard char truncation guard (Tier 4 pre-step)
        if len(tool_output) > _MAX_CHARS_BEFORE_COMPACTION:
            original_chars = len(tool_output)
            tool_output = tool_output[:_MAX_CHARS_BEFORE_COMPACTION]
            tool_output += f"\n\n[Truncated: {original_chars} chars → {_MAX_CHARS_BEFORE_COMPACTION}]"
            logger.debug("Hard-truncated tool output from %d chars", original_chars)

        estimated = self.estimate_tokens(tool_output)
        threshold = self.config.compaction_threshold

        # Tier 1: small output — pass through
        if estimated <= threshold:
            return tool_output

        # Tier 4: very large — hard-truncate to char limit, then summarize
        if estimated > _TIER3_THRESHOLD:
            if len(tool_output) > _TIER4_CHARS:
                tool_output = tool_output[:_TIER4_CHARS]
                tool_output += f"\n\n[Truncated to {_TIER4_CHARS} chars for summarization]"
            return await self._summarize(
                tool_output, llm_client, token_tracker, agent_name,
                tier=4, full=True
            )

        # Tier 3: large — full summarization
        if estimated > _TIER2_THRESHOLD:
            return await self._summarize(
                tool_output, llm_client, token_tracker, agent_name,
                tier=3, full=True
            )

        # Tier 2: medium — key facts extraction
        return await self._summarize(
            tool_output, llm_client, token_tracker, agent_name,
            tier=2, full=False
        )

    async def _summarize(
        self,
        text: str,
        llm_client,
        token_tracker,
        agent_name: str,
        tier: int,
        full: bool,
    ) -> str:
        logger.debug("Compacting tool output (tier %d): ~%d tokens", tier, self.estimate_tokens(text))

        if full:
            prompt = (
                "Summarize this tool output concisely, preserving all key information "
                "including file paths, function names, error messages, line numbers, "
                "and important values:\n\n" + text
            )
            max_tok = 1024
        else:
            prompt = (
                "Extract the key facts from this output as a compact bullet list. "
                "Include: file paths, function names, key values, errors, line numbers. "
                "Omit boilerplate and repetition:\n\n" + text
            )
            max_tok = 512

        try:
            response = await llm_client.chat(
                model=self.config.compaction_model,
                system="You are a concise technical summarizer.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tok,
            )
        except Exception as e:
            logger.warning("Compaction LLM call failed (tier %d): %s", tier, e)
            return text  # return original on failure

        if token_tracker and agent_name:
            token_tracker.add(
                f"{agent_name}/_compaction",
                response.input_tokens,
                response.output_tokens,
                self.config.compaction_model,
            )

        return f"[Compacted tier-{tier}]\n{response.content}"

    async def compact_injected_context(
        self,
        context: str,
        llm_client,
        token_tracker=None,
        agent_name: str = "",
    ) -> str:
        """
        Compact injected context (explorer summary, etc.) if it exceeds
        max_context_injection_pct of the history budget.
        """
        max_budget_tokens = self.config.max_history_tokens
        max_inject_tokens = int(max_budget_tokens * self.config.max_context_injection_pct)
        estimated = self.estimate_tokens(context)

        if estimated <= max_inject_tokens:
            return context

        logger.debug(
            "Context injection too large (%d tokens > %d allowed) — compacting",
            estimated, max_inject_tokens
        )
        if token_tracker and agent_name:
            # Track as context injection overhead
            return await self._summarize(
                context, llm_client, token_tracker,
                f"{agent_name}/_context_injection", tier=3, full=True
            )
        return await self._summarize(
            context, llm_client, token_tracker, agent_name, tier=3, full=True
        )

    # ------------------------------------------------------------------ #
    #  Smart history trimming                                              #
    # ------------------------------------------------------------------ #

    def trim_history(
        self,
        messages: list[dict],
        max_tokens: Optional[int] = None,
    ) -> list[dict]:
        """
        Trim conversation history to fit within the token budget.

        Importance scoring:
        - First message (task):             always kept
        - Last assistant message:           always kept
        - Messages with file edits/writes:  high importance (kept)
        - Tool-result messages:             medium importance
        - Other assistant messages:         low importance (dropped first)
        """
        limit = max_tokens or self.config.max_history_tokens
        total = sum(self._estimate_message_tokens(m) for m in messages)

        if total <= limit or len(messages) < 2:
            return messages

        first_msg = messages[0]
        middle = list(messages[1:])

        # Find last assistant message index (preserve it)
        last_assistant_idx = max(
            (i for i, m in enumerate(middle) if m.get("role") == "assistant"),
            default=None,
        )

        # Score each middle message (higher = more important = keep longer)
        def _score(i: int, msg: dict) -> int:
            if i == last_assistant_idx:
                return 100  # always keep last assistant
            content = str(msg.get("content", ""))
            if any(kw in content for kw in ("write_file", "edit_file", "str_replace", "create_file")):
                return 80   # file edits are critical
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                return 50   # tool results
            if msg.get("role") == "assistant":
                return 30   # regular assistant messages
            return 20       # other user messages

        scored = [(i, _score(i, m), m) for i, m in enumerate(middle)]
        # Drop lowest-importance messages first (stable sort preserves order for equal scores)
        scored.sort(key=lambda x: x[1])

        drop_idx = set()
        for i, score, msg in scored:
            if total <= limit:
                break
            if score >= 80:
                break  # don't drop high-importance messages
            drop_idx.add(i)
            total -= self._estimate_message_tokens(msg)
            logger.debug("Trimmed message (score=%d, role=%s)", score, msg.get("role"))

        remaining = [m for i, m in enumerate(middle) if i not in drop_idx]

        trimmed_note: dict = {
            "role": "user",
            "content": "[Earlier conversation context trimmed to fit context window]",
        }
        result = [first_msg, trimmed_note] + remaining
        logger.debug("History trimmed: %d → %d messages", len(messages), len(result))
        return result

    # ------------------------------------------------------------------ #
    #  Token estimation helpers                                            #
    # ------------------------------------------------------------------ #

    def _estimate_message_tokens(self, message: dict) -> int:
        """Estimate token count for a single message (any content shape)."""
        content = message.get("content", "")
        if isinstance(content, str):
            return self.estimate_tokens(content)
        if isinstance(content, list):
            return self.estimate_tokens(json.dumps(content))
        return 0

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token for English."""
        return max(1, len(text) // 4)
