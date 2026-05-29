"""
LLM Client — wraps Anthropic API.

Handles:
- Async API calls with tool use support
- Parsing responses (text + tool_use blocks)
- Preserving raw content blocks for message replay
- Token count reporting for tracking
"""

import logging
from dataclasses import dataclass, field

import anthropic

from src.config import ArgusConfig

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Parsed response from LLM API call."""
    content: str                  # concatenated text from all text blocks
    tool_calls: list[dict]        # list of {id, name, input}
    raw_content: list[dict]       # serialized content blocks for message replay
    stop_reason: str              # "end_turn" | "tool_use" | "max_tokens"
    input_tokens: int
    output_tokens: int
    model: str


class LLMClient:
    def __init__(self, config: ArgusConfig):
        self.config = config
        kwargs: dict = {
            "api_key": config.anthropic_api_key,
            "max_retries": config.api_max_retries,
            "timeout": float(config.api_timeout),
        }
        if config.api_base_url:
            kwargs["base_url"] = config.api_base_url
        self.client = anthropic.AsyncAnthropic(**kwargs)

    async def chat(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
    ) -> LLMResponse:
        """
        Send a message to the LLM and return parsed response.

        Args:
            model: Model identifier (e.g. "claude-sonnet-4-20250514")
            system: System prompt
            messages: Conversation history [{role, content}, ...]
            tools: Tool definitions for tool use (omit key if empty)
            max_tokens: Max response tokens (default raised to 8192)

        Returns:
            LLMResponse with parsed content, raw blocks, tool calls, and token counts
        """
        params: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        # Only include tools if non-empty — sending tools=[] can cause API errors
        if tools:
            params["tools"] = tools

        logger.debug("LLM call: model=%s, messages=%d", model, len(messages))

        response = await self.client.messages.create(**params)

        parsed = self._parse_response(response)
        logger.debug(
            "LLM response: stop_reason=%s, in=%d, out=%d, tool_calls=%d",
            parsed.stop_reason,
            parsed.input_tokens,
            parsed.output_tokens,
            len(parsed.tool_calls),
        )
        return parsed

    async def close(self):
        """Close the underlying HTTP client to avoid ResourceWarning."""
        await self.client.close()

    def _parse_response(self, response) -> LLMResponse:
        """Parse Anthropic API response into LLMResponse."""
        content_text = ""
        tool_calls = []
        raw_content = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
                raw_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                raw_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            raw_content=raw_content,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )
