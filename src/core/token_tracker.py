"""
Token Tracker — real-time cost monitoring.

Tracks per-agent token usage, calculates USD cost,
fires warnings at threshold, enforces hard cap.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from src.config import TokenBudget

if TYPE_CHECKING:
    from src.core.pricing import ModelPricing


@dataclass
class AgentUsage:
    """Token usage for a single agent."""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


@dataclass
class TokenTracker:
    budget: TokenBudget
    pricing: Optional["ModelPricing"] = field(default=None, repr=False)
    usage: dict[str, AgentUsage] = field(default_factory=dict)
    total_input: int = 0
    total_output: int = 0
    total_cost: float = 0.0
    warnings_fired: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    # Last-task snapshot for per-task cost display (Step 2)
    _last_snapshot: Optional[dict] = field(default=None, repr=False)

    def _get_price(self, model: str) -> dict:
        """Get model pricing, using live registry if available."""
        if self.pricing is not None:
            return self.pricing.get_price(model)
        # Inline fallback if no pricing registry attached yet
        _FALLBACK = {
            "claude-sonnet-4-20250514":  {"input": 3.00, "output": 15.00},
            "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
            "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
        }
        return _FALLBACK.get(model, {"input": 3.00, "output": 15.00})

    def add(self, agent_name: str, input_tokens: int, output_tokens: int, model: str):
        """Record token usage from an API call."""
        if agent_name not in self.usage:
            self.usage[agent_name] = AgentUsage()

        pricing = self._get_price(model)
        cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

        agent = self.usage[agent_name]
        agent.input_tokens += input_tokens
        agent.output_tokens += output_tokens
        agent.cost_usd += cost
        agent.calls += 1

        self.total_input += input_tokens
        self.total_output += output_tokens
        self.total_cost += cost

        self._check_warnings(agent_name)

    def snapshot(self) -> dict:
        """Return a point-in-time snapshot of totals (for per-task delta computation)."""
        return {
            "total_tokens": self.total_input + self.total_output,
            "total_cost_usd": self.total_cost,
        }

    def is_hard_cap_reached(self) -> bool:
        """Check if total tokens OR cost exceed hard caps."""
        token_cap = (self.total_input + self.total_output) >= self.budget.total_hard_cap
        dollar_cap = (
            self.budget.dollar_hard_cap > 0
            and self.total_cost >= self.budget.dollar_hard_cap
        )
        return token_cap or dollar_cap

    def is_soft_cap_reached(self) -> bool:
        """Check if total tokens OR cost exceed soft caps."""
        total_tokens = self.total_input + self.total_output
        token_soft = (
            self.budget.total_soft_cap > 0
            and total_tokens >= self.budget.total_soft_cap
        )
        dollar_soft = (
            self.budget.dollar_soft_cap > 0
            and self.total_cost >= self.budget.dollar_soft_cap
        )
        return token_soft or dollar_soft

    def is_agent_cap_reached(self, agent_name: str) -> bool:
        """Check if a specific agent exceeded its token budget."""
        agent_cap = self.budget.per_agent.get(agent_name)
        if not agent_cap:
            return False
        agent = self.usage.get(agent_name, AgentUsage())
        return (agent.input_tokens + agent.output_tokens) >= agent_cap

    def set_budget(self, field_name: str, value: float) -> bool:
        """
        Update a budget field at runtime. Returns True if the field was valid.
        field_name: total_hard_cap | total_soft_cap | dollar_hard_cap | dollar_soft_cap
        """
        valid_fields = {
            "total_hard_cap", "total_soft_cap",
            "dollar_hard_cap", "dollar_soft_cap",
        }
        if field_name not in valid_fields:
            return False
        if field_name in ("total_hard_cap", "total_soft_cap"):
            setattr(self.budget, field_name, int(value))
        else:
            setattr(self.budget, field_name, float(value))
        return True

    def _check_warnings(self, agent_name: str):
        """Fire warnings if approaching hard limits."""
        total_used = self.total_input + self.total_output
        threshold = self.budget.total_hard_cap * self.budget.warning_threshold

        if total_used >= threshold and "total_warning" not in self.warnings_fired:
            self.warnings_fired.append("total_warning")

        if self.is_soft_cap_reached() and "soft_cap" not in self.warnings_fired:
            self.warnings_fired.append("soft_cap")

    def get_summary(self) -> dict:
        """Return current usage summary for display."""
        total_used = self.total_input + self.total_output
        cap = max(self.budget.total_hard_cap, 1)

        return {
            "total_tokens": total_used,
            "total_cost_usd": round(self.total_cost, 6),
            "hard_cap": self.budget.total_hard_cap,
            "soft_cap": self.budget.total_soft_cap,
            "dollar_hard_cap": self.budget.dollar_hard_cap,
            "dollar_soft_cap": self.budget.dollar_soft_cap,
            "percent_used": round(total_used / cap * 100, 1),
            "warnings": list(self.warnings_fired),
            "per_agent": {
                name: {
                    "tokens": a.input_tokens + a.output_tokens,
                    "cost": round(a.cost_usd, 6),
                    "calls": a.calls,
                }
                for name, a in self.usage.items()
            },
        }
