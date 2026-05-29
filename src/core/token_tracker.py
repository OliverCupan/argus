"""
Token Tracker — real-time cost monitoring.

Tracks per-agent token usage, calculates USD cost,
fires warnings at threshold, enforces hard cap.
"""

from dataclasses import dataclass, field
from datetime import datetime

from src.config import TokenBudget


# Approximate pricing per 1M tokens (input/output) as of 2025
MODEL_PRICING = {
    "claude-sonnet-4-20250514":   {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80, "output": 4.00},
}

# Fallback for unknown models
DEFAULT_PRICING = {"input": 3.00, "output": 15.00}


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
    usage: dict[str, AgentUsage] = field(default_factory=dict)
    total_input: int = 0
    total_output: int = 0
    total_cost: float = 0.0
    warnings_fired: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)

    def add(self, agent_name: str, input_tokens: int, output_tokens: int, model: str):
        """Record token usage from an API call."""

        if agent_name not in self.usage:
            self.usage[agent_name] = AgentUsage()

        pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
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

    def is_hard_cap_reached(self) -> bool:
        """Check if total tokens exceed hard cap."""
        return (self.total_input + self.total_output) >= self.budget.total_hard_cap

    def is_agent_cap_reached(self, agent_name: str) -> bool:
        """Check if a specific agent exceeded its budget."""
        agent_cap = self.budget.per_agent.get(agent_name)
        if not agent_cap:
            return False
        agent = self.usage.get(agent_name, AgentUsage())
        return (agent.input_tokens + agent.output_tokens) >= agent_cap

    def _check_warnings(self, agent_name: str):
        """Fire warnings if approaching budget limits."""
        total_used = self.total_input + self.total_output
        threshold = self.budget.total_hard_cap * self.budget.warning_threshold

        if total_used >= threshold and "total_warning" not in self.warnings_fired:
            self.warnings_fired.append("total_warning")

    def get_summary(self) -> dict:
        """Return current usage summary for display."""
        total_used = self.total_input + self.total_output
        # Guard against division by zero from misconfigured YAML
        cap = max(self.budget.total_hard_cap, 1)

        return {
            "total_tokens": total_used,
            "total_cost_usd": round(self.total_cost, 4),
            "hard_cap": self.budget.total_hard_cap,
            "percent_used": round(total_used / cap * 100, 1),
            "warnings": list(self.warnings_fired),
            "per_agent": {
                name: {
                    "tokens": a.input_tokens + a.output_tokens,
                    "cost": round(a.cost_usd, 4),
                    "calls": a.calls,
                }
                for name, a in self.usage.items()
            },
        }
