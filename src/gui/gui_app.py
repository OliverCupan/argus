"""
GuiApp — web-mode equivalent of ArgusCliApp.

Wires the Orchestrator to the EventBus, provides web-compatible
confirm/status callbacks, and exposes a handle_command() method
that the FastAPI server calls for each user request.
"""

import asyncio
import dataclasses
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from src.config import ArgusConfig, AGENT_NAMES
from src.core.token_tracker import TokenTracker
from src.core.pricing import ModelPricing
from src.agents.orchestrator import Orchestrator
from src.gui.event_bus import EventBus

logger = logging.getLogger(__name__)

# Agents whose model can be swapped at runtime (imported from config)
_AGENT_NAMES = AGENT_NAMES

_BUDGET_FIELDS = {
    "total_hard_cap", "total_soft_cap", "dollar_hard_cap", "dollar_soft_cap",
}


class GuiApp:
    """
    Thin web adapter over Orchestrator.

    The FastAPI server holds one GuiApp instance for the lifetime of
    the process.  All WebSocket clients share the same EventBus.
    """

    def __init__(
        self,
        config: ArgusConfig,
        pricing: Optional[ModelPricing],
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.tracker = TokenTracker(config.token_budget, pricing=pricing)
        self.pricing = pricing

        # Pending confirm requests: request_id → (asyncio.Event, result_holder)
        self._pending_confirms: dict[str, tuple[asyncio.Event, list]] = {}

        self.orchestrator = Orchestrator(
            config,
            self.tracker,
            confirm_callback=self._web_confirm,
            status_callback=None,  # Orchestrator's _status_with_emit handles GUI emission
            event_bus=event_bus,
        )

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    async def _web_confirm(self, command: str) -> bool:
        """
        Non-blocking confirm: emit confirm_required, then wait for the
        browser to POST /api/confirm/{request_id}.
        """
        import uuid
        request_id = str(uuid.uuid4())
        event = asyncio.Event()
        result: list[bool] = [False]
        self._pending_confirms[request_id] = (event, result)

        await self.event_bus.emit(
            "orchestrator",
            "confirm_required",
            command=command,
            request_id=request_id,
        )

        try:
            await asyncio.wait_for(event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("Confirm request %s timed out — denying", request_id)

        self._pending_confirms.pop(request_id, None)
        return result[0]

    def resolve_confirm(self, request_id: str, approved: bool) -> bool:
        """Called by the FastAPI /api/confirm endpoint."""
        entry = self._pending_confirms.get(request_id)
        if entry is None:
            return False
        event, result = entry
        result[0] = approved
        event.set()
        return True

    # ------------------------------------------------------------------ #
    #  Command handling                                                    #
    # ------------------------------------------------------------------ #

    async def handle_command(self, text: str) -> str:
        """
        Route a raw user command exactly as the CLI does, then return
        the markdown result string.  Events are streamed via EventBus
        during execution.
        """
        stripped = text.strip()
        cmd = stripped.lower()

        # Built-in non-agent commands
        if cmd in ("stats", "stat"):
            return self._stats_markdown()
        if cmd == "budget":
            return self._budget_markdown()
        if cmd in ("exit", "quit"):
            return "_Use the browser tab to close Argus._"

        # budget set <field> <value>
        m = re.match(r"^budget\s+set\s+(\S+)\s+(\S+)$", stripped, re.IGNORECASE)
        if m:
            return self._handle_budget_set(m.group(1), m.group(2))

        # model (list) / model <agent> <name>
        if cmd == "model":
            return self._models_markdown()
        m = re.match(r"^model\s+(\S+)\s+(\S+)$", stripped, re.IGNORECASE)
        if m:
            return self._handle_model_set(m.group(1), m.group(2))

        # fix <id>
        m = re.match(r"^fix\s+(\d+)\s*$", stripped, re.IGNORECASE)
        if m:
            return await self.orchestrator.fix_finding(int(m.group(1)))

        # Everything else → orchestrator (coding or audit)
        cwd = os.getcwd()
        augmented = f"[Working directory: {cwd}]\n\n{stripped}"

        # Emit token snapshot before so the browser can compute a delta
        await self.event_bus.emit(
            "orchestrator", "token_update",
            summary=self.tracker.get_summary(),
        )

        result = await self.orchestrator.handle(augmented)

        # Emit final token snapshot
        await self.event_bus.emit(
            "orchestrator", "token_update",
            summary=self.tracker.get_summary(),
        )
        return result

    # ------------------------------------------------------------------ #
    #  Info helpers                                                        #
    # ------------------------------------------------------------------ #

    def get_stats(self) -> dict:
        return self.tracker.get_summary()

    def get_config(self) -> dict:
        models = dataclasses.asdict(self.config.models)
        budget = dataclasses.asdict(self.config.token_budget)
        return {"models": models, "budget": budget, "agents": _AGENT_NAMES}

    def _stats_markdown(self) -> str:
        s = self.tracker.get_summary()
        lines = ["## Token Usage\n", "| Agent | Tokens | Cost | Calls |", "|---|---|---|---|"]
        for name, d in s["per_agent"].items():
            lines.append(f"| {name} | {d['tokens']:,} | ${d['cost']:.6f} | {d['calls']} |")
        lines.append(f"\n**Total:** {s['total_tokens']:,} tokens · ${s['total_cost_usd']:.4f} · {s['percent_used']}% of budget")
        return "\n".join(lines)

    def _budget_markdown(self) -> str:
        b = self.config.token_budget
        s = self.tracker.get_summary()
        used_t = s["total_tokens"]
        used_d = s["total_cost_usd"]
        lines = [
            "## Budget\n",
            "| Limit | Cap | Used |",
            "|---|---|---|",
            f"| Token hard cap | {b.total_hard_cap:,} | {used_t:,} |",
            f"| Token soft cap | {b.total_soft_cap:,} | {used_t:,} |",
            f"| Dollar hard cap | ${b.dollar_hard_cap:.2f} | ${used_d:.4f} |",
            f"| Dollar soft cap | ${b.dollar_soft_cap:.2f} | ${used_d:.4f} |",
            f"\nTo adjust: `budget set <field> <value>`",
        ]
        return "\n".join(lines)

    def _models_markdown(self) -> str:
        models = dataclasses.asdict(self.config.models)
        lines = ["## Agent Models\n", "| Agent | Model |", "|---|---|"]
        for a in _AGENT_NAMES:
            lines.append(f"| {a} | {models.get(a, '—')} |")
        lines.append("\nTo change: `model <agent> <model-name>`")
        return "\n".join(lines)

    def _handle_budget_set(self, field: str, value_str: str) -> str:
        if field not in _BUDGET_FIELDS:
            return f"Unknown budget field `{field}`. Valid: {', '.join(_BUDGET_FIELDS)}"
        try:
            typ = int if field.startswith("total") else float
            value = typ(value_str)
        except ValueError:
            return f"Invalid value `{value_str}` for `{field}`"
        ok = self.tracker.set_budget(field, value)
        return f"Budget `{field}` updated to `{value}`." if ok else f"Failed to update `{field}`."

    def _handle_model_set(self, agent: str, model_name: str) -> str:
        if agent not in _AGENT_NAMES:
            return f"Unknown agent `{agent}`. Valid: {', '.join(_AGENT_NAMES)}"
        ok = self.orchestrator.set_model(agent, model_name)
        return f"Model for `{agent}` updated to `{model_name}`." if ok else f"Failed to update `{agent}`."
