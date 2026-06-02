"""
Configuration loader.

Reads argus.yaml and validates required fields.
API keys come from environment variables (loaded via .env).
"""

import os
from pathlib import Path
from dataclasses import dataclass, field

import yaml


@dataclass
class ModelConfig:
    orchestrator: str = ""
    challenger: str = ""
    coder: str = ""
    explorer: str = ""
    security_auditor: str = ""
    bug_auditor: str = ""
    performance_auditor: str = ""
    test_auditor: str = ""


@dataclass
class TokenBudget:
    total_hard_cap: int = 500_000      # tokens — kill immediately
    total_soft_cap: int = 400_000      # tokens — warn, let agent wind down
    dollar_hard_cap: float = 5.00      # USD — kill immediately (0 = disabled)
    dollar_soft_cap: float = 4.00      # USD — warn (0 = disabled)
    warning_threshold: float = 0.8    # legacy threshold (kept for compat)
    per_agent: dict = field(default_factory=dict)


@dataclass
class SafetyConfig:
    blocked_commands: list = field(default_factory=list)
    review_patterns: list = field(default_factory=list)
    allowed_write_paths: list = field(default_factory=lambda: ["."])


@dataclass
class ContextConfig:
    max_history_tokens: int = 50000
    compaction_threshold: int = 1000          # Tier 1/2 boundary (Step 7)
    compaction_model: str = "claude-3-5-haiku-20241022"
    max_context_injection_pct: float = 0.30   # max % of history for injected context


@dataclass
class AgentConfig:
    max_iterations: int = 15
    bash_timeout: int = 30
    parallel_audit: bool = True
    use_worktrees: bool = False               # opt-in git worktree isolation
    worktree_dir: str = ".argus/worktrees"


@dataclass
class ArgusConfig:
    models: ModelConfig = field(default_factory=ModelConfig)
    api_provider: str = "anthropic"
    api_base_url: str | None = None
    api_max_retries: int = 3
    api_timeout: int = 60
    token_budget: TokenBudget = field(default_factory=TokenBudget)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    # From environment
    anthropic_api_key: str = ""
    openai_api_key: str = ""


def load_config(path: Path) -> ArgusConfig:
    """Load config from YAML file and environment variables."""

    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raw = {}

    config = ArgusConfig()

    # Models
    models = raw.get("models", {})
    config.models = ModelConfig(**{k: v for k, v in models.items() if hasattr(ModelConfig, k)})

    # API
    api = raw.get("api", {})
    config.api_provider = api.get("provider", "anthropic")
    config.api_base_url = api.get("base_url")
    config.api_max_retries = api.get("max_retries", 3)
    config.api_timeout = api.get("timeout", 60)

    # Token budget
    budget = raw.get("token_budget", {})
    config.token_budget = TokenBudget(
        total_hard_cap=budget.get("total_hard_cap", 500_000),
        total_soft_cap=budget.get("total_soft_cap", 400_000),
        dollar_hard_cap=float(budget.get("dollar_hard_cap", 5.00)),
        dollar_soft_cap=float(budget.get("dollar_soft_cap", 4.00)),
        warning_threshold=budget.get("warning_threshold", 0.8),
        per_agent=budget.get("per_agent", {}),
    )

    # Safety
    safety = raw.get("safety", {})
    config.safety = SafetyConfig(
        blocked_commands=safety.get("blocked_commands", []),
        review_patterns=safety.get("review_patterns", []),
        allowed_write_paths=safety.get("allowed_write_paths", ["."]),
    )

    # Context
    ctx = raw.get("context", {})
    config.context = ContextConfig(
        max_history_tokens=ctx.get("max_history_tokens", 50000),
        compaction_threshold=ctx.get("compaction_threshold", 1000),
        compaction_model=ctx.get("compaction_model", "claude-3-5-haiku-20241022"),
        max_context_injection_pct=float(ctx.get("max_context_injection_pct", 0.30)),
    )

    # Agent
    agent = raw.get("agent", {})
    config.agent = AgentConfig(
        max_iterations=agent.get("max_iterations", 15),
        bash_timeout=agent.get("bash_timeout", 30),
        parallel_audit=agent.get("parallel_audit", True),
        use_worktrees=agent.get("use_worktrees", False),
        worktree_dir=agent.get("worktree_dir", ".argus/worktrees"),
    )

    # API keys from environment
    config.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    config.openai_api_key = os.getenv("OPENAI_API_KEY", "")

    _validate(config)
    return config


def _validate(config: ArgusConfig):
    """Validate that required config is present."""

    if config.api_provider == "anthropic" and not config.anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set.\n"
            "  Copy .env.example to .env and add your key:\n"
            "  ANTHROPIC_API_KEY=sk-ant-..."
        )
    if config.api_provider == "openai" and not config.openai_api_key:
        raise ValueError("OPENAI_API_KEY not set in environment")

    if not config.models.coder:
        raise ValueError(
            "models.coder must be set in argus.yaml.\n"
            "  Example: coder: claude-sonnet-4-20250514"
        )
