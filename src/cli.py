"""
CLI interface for Argus — v2.

Commands:
  <free-form>              Coding task: Explorer → Challenger → Coder → Auto-Audit
  audit <path>             Parallel security/bug/perf/test scan
  fix <id>                 Apply fix for a specific audit finding
  budget                   Show current budget limits and usage
  budget set <field> <val> Adjust a budget limit live
  model                    Show current model assignments
  model <agent> <name>     Switch an agent's model live
  stats                    Per-agent token and cost table
  exit                     Quit
"""

import asyncio
import dataclasses
import itertools
import os
import re
import sys
from typing import Coroutine, Any, Optional

from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config import ArgusConfig, AGENT_NAMES
from src.core.token_tracker import TokenTracker
from src.core.pricing import ModelPricing
from src.agents.orchestrator import Orchestrator
from src.ui.eye import get_eye

console = Console()

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_SPINNER_WIDTH = 120

_SEV_STYLES = {
    "CRITICAL": "bold red",
    "HIGH":     "bold yellow",
    "MEDIUM":   "bold cyan",
    "LOW":      "dim",
}

# Agents that can have their model changed at runtime (imported from config)
_AGENT_NAMES = AGENT_NAMES

# Budget fields editable at runtime
_BUDGET_FIELDS = {
    "total_hard_cap":  ("Token hard cap (tokens)",  int),
    "total_soft_cap":  ("Token soft cap (tokens)",  int),
    "dollar_hard_cap": ("Dollar hard cap (USD)",    float),
    "dollar_soft_cap": ("Dollar soft cap (USD)",    float),
}


class ArgusCliApp:
    def __init__(self, config: ArgusConfig, pricing: Optional[ModelPricing] = None):
        self.config = config
        self.pricing = pricing
        self.token_tracker = TokenTracker(config.token_budget, pricing=pricing)
        self._current_status = "Working…"
        self._spinner_paused = False
        self._pricing_status = pricing.status_line() if pricing else "Pricing: bundled"
        self._last_task_tokens = 0
        self._last_task_cost = 0.0

        def _clear_spinner_line() -> None:
            sys.stdout.write("\r" + " " * _SPINNER_WIDTH + "\r")
            sys.stdout.flush()

        async def confirm_callback(command: str) -> bool:
            self._spinner_paused = True
            await asyncio.sleep(0.15)
            _clear_spinner_line()
            console.print(
                f"\n[yellow bold]  REVIEW REQUIRED[/yellow bold]\n"
                f"  The agent wants to run:\n"
                f"  [bold]{command}[/bold]"
            )
            answer = input("  Allow? [y/N] ").strip().lower()
            console.print()
            self._spinner_paused = False
            return answer in ("y", "yes")

        async def status_callback(message: str) -> None:
            self._current_status = message

        self.orchestrator = Orchestrator(
            config,
            self.token_tracker,
            confirm_callback=confirm_callback,
            status_callback=status_callback,
        )

    async def run(self):
        self._print_banner()
        try:
            await self._input_loop()
        finally:
            await self.orchestrator.close()

    # ------------------------------------------------------------------ #
    #  Agent runner with spinner                                           #
    # ------------------------------------------------------------------ #

    async def _run_agent(self, coro: Coroutine[Any, Any, str]) -> str | None:
        """Run an agent coroutine with inline spinner. Returns result or None."""
        self._current_status = "Starting…"

        async def _spinner_task() -> None:
            frames = itertools.cycle(_SPINNER_FRAMES)
            while True:
                if not self._spinner_paused:
                    frame = next(frames)
                    summary = self.token_tracker.get_summary()
                    cost_str = f"${summary['total_cost_usd']:.4f} · {summary['total_tokens']:,}t"
                    line = f"\r  {frame}  {self._current_status}  [{cost_str}]"
                    sys.stdout.write(line.ljust(_SPINNER_WIDTH))
                    sys.stdout.flush()
                await asyncio.sleep(0.12)

        orig_status = self.orchestrator._status

        async def live_status(message: str) -> None:
            self._current_status = message

        self.orchestrator._status = live_status
        spinner = asyncio.create_task(_spinner_task())
        before = self.token_tracker.snapshot()
        try:
            result = await coro
            return result
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            return None
        finally:
            spinner.cancel()
            self.orchestrator._status = orig_status
            after = self.token_tracker.snapshot()
            self._last_task_tokens = after["total_tokens"] - before["total_tokens"]
            self._last_task_cost = after["total_cost_usd"] - before["total_cost_usd"]
            sys.stdout.write("\r" + " " * _SPINNER_WIDTH + "\r")
            sys.stdout.flush()

    # ------------------------------------------------------------------ #
    #  Input loop                                                          #
    # ------------------------------------------------------------------ #

    async def _input_loop(self):
        while True:
            try:
                user_input = console.input("\n[bold cyan]argus >[/bold cyan] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Goodbye.[/dim]")
                return

            if not user_input:
                continue
            cmd = user_input.lower()

            if cmd in ("exit", "quit"):
                console.print("[dim]Goodbye.[/dim]")
                return

            if cmd == "stats":
                self._print_stats()
                continue

            # budget / budget set <field> <value>
            if cmd == "budget":
                self._print_budget()
                continue
            budget_set = re.match(r"^budget\s+set\s+(\S+)\s+(\S+)$", user_input, re.IGNORECASE)
            if budget_set:
                self._handle_budget_set(budget_set.group(1), budget_set.group(2))
                continue

            # model / model <agent> <name>
            if cmd == "model":
                self._print_models()
                continue
            model_set = re.match(r"^model\s+(\S+)\s+(\S+)$", user_input, re.IGNORECASE)
            if model_set:
                self._handle_model_set(model_set.group(1), model_set.group(2))
                continue

            # fix <id>
            fix_match = re.match(r"^fix\s+(\d+)\s*$", user_input, re.IGNORECASE)
            if fix_match:
                finding_id = int(fix_match.group(1))
                result_text = await self._run_agent(self.orchestrator.fix_finding(finding_id))
                if result_text:
                    self._print_response(result_text)
                    self._print_token_line()
                continue

            # audit <path> — handled before WD augmentation so the regex in
            # orchestrator.handle() sees the raw "audit ..." prefix, not the header.
            audit_match = re.match(r"^audit\b(.*)", user_input, re.IGNORECASE | re.DOTALL)
            if audit_match:
                target = audit_match.group(1).strip() or "."
                cwd = os.getcwd()
                augmented = f"[Working directory: {cwd}]\n\naudit {target}"
                result_text = await self._run_agent(self.orchestrator.handle(augmented))
                if result_text is None:
                    continue
                self._print_response(result_text)
                self._print_token_line()
                continue

            # All other inputs — coding tasks
            cwd = os.getcwd()
            augmented = f"[Working directory: {cwd}]\n\n{user_input}"
            result_text = await self._run_agent(self.orchestrator.handle(augmented))
            if result_text is None:
                continue

            self._print_response(result_text)
            self._print_token_line()

    # ------------------------------------------------------------------ #
    #  Budget commands (Step 4)                                            #
    # ------------------------------------------------------------------ #

    def _print_budget(self):
        summary = self.token_tracker.get_summary()
        b = self.config.token_budget
        used_tokens = summary["total_tokens"]
        used_dollars = summary["total_cost_usd"]

        table = Table(title="Budget", border_style="cyan", show_lines=True)
        table.add_column("Limit", style="cyan")
        table.add_column("Value", justify="right")
        table.add_column("Used", justify="right")
        table.add_column("Status", justify="right")

        def _pct(used, cap):
            if cap <= 0:
                return "—"
            return f"{used / cap * 100:.1f}%"

        def _status(used, cap, is_hard):
            if cap <= 0:
                return "[dim]disabled[/dim]"
            if used >= cap:
                return "[red]EXCEEDED[/red]" if is_hard else "[yellow]SOFT LIMIT[/yellow]"
            return "[green]OK[/green]"

        table.add_row(
            "Token hard cap",
            f"{b.total_hard_cap:,}",
            f"{used_tokens:,}  ({_pct(used_tokens, b.total_hard_cap)})",
            _status(used_tokens, b.total_hard_cap, True),
        )
        table.add_row(
            "Token soft cap",
            f"{b.total_soft_cap:,}",
            f"{used_tokens:,}  ({_pct(used_tokens, b.total_soft_cap)})",
            _status(used_tokens, b.total_soft_cap, False),
        )
        table.add_row(
            "Dollar hard cap",
            f"${b.dollar_hard_cap:.2f}" if b.dollar_hard_cap > 0 else "disabled",
            f"${used_dollars:.4f}  ({_pct(used_dollars, b.dollar_hard_cap)})" if b.dollar_hard_cap > 0 else "—",
            _status(used_dollars, b.dollar_hard_cap, True) if b.dollar_hard_cap > 0 else "[dim]—[/dim]",
        )
        table.add_row(
            "Dollar soft cap",
            f"${b.dollar_soft_cap:.2f}" if b.dollar_soft_cap > 0 else "disabled",
            f"${used_dollars:.4f}  ({_pct(used_dollars, b.dollar_soft_cap)})" if b.dollar_soft_cap > 0 else "—",
            _status(used_dollars, b.dollar_soft_cap, False) if b.dollar_soft_cap > 0 else "[dim]—[/dim]",
        )
        console.print(table)
        console.print(
            "[dim]  To adjust: [/dim][cyan]budget set <field> <value>[/cyan]  "
            "[dim]Fields: total_hard_cap  total_soft_cap  dollar_hard_cap  dollar_soft_cap[/dim]"
        )

    def _handle_budget_set(self, field: str, value_str: str):
        if field not in _BUDGET_FIELDS:
            console.print(
                f"[red]Unknown budget field:[/red] {field!r}\n"
                f"  Valid: {', '.join(_BUDGET_FIELDS)}"
            )
            return
        try:
            _, typ = _BUDGET_FIELDS[field]
            new_val = typ(value_str)
        except ValueError:
            console.print(f"[red]Invalid value:[/red] {value_str!r} — expected {_BUDGET_FIELDS[field][1].__name__}")
            return

        old_val = getattr(self.config.token_budget, field)
        ok = self.token_tracker.set_budget(field, new_val)
        if ok:
            console.print(f"[green]  {_BUDGET_FIELDS[field][0]} updated:[/green] {old_val} → {new_val}")
        else:
            console.print(f"[red]  Failed to update {field}[/red]")

    # ------------------------------------------------------------------ #
    #  Model commands (Step 5)                                             #
    # ------------------------------------------------------------------ #

    def _print_models(self):
        models = dataclasses.asdict(self.config.models)
        table = Table(title="Agent Models", border_style="cyan", show_lines=True)
        table.add_column("Agent", style="cyan")
        table.add_column("Model")
        table.add_column("Cost / 1M tokens", justify="right", style="dim")

        pricing_src = self.pricing or self.token_tracker  # prefer live registry

        for agent in _AGENT_NAMES:
            model_name = models.get(agent) or ""
            if model_name and self.pricing is not None:
                price = self.pricing.get_price(model_name)
                cost_str = f"${price.get('input', 0):.2f} in / ${price.get('output', 0):.2f} out"
            elif model_name:
                price = self.token_tracker._get_price(model_name)
                cost_str = f"${price.get('input', 0):.2f} in / ${price.get('output', 0):.2f} out"
            else:
                cost_str = "[dim]—[/dim]"
            table.add_row(agent, model_name or "[dim]not set[/dim]", cost_str)

        console.print(table)
        console.print("[dim]  To change: [/dim][cyan]model <agent> <model-name>[/cyan]")

    def _handle_model_set(self, agent: str, model_name: str):
        if agent not in _AGENT_NAMES:
            console.print(
                f"[red]Unknown agent:[/red] {agent!r}\n"
                f"  Valid: {', '.join(_AGENT_NAMES)}"
            )
            return

        # Validate against pricing registry if available
        if self.pricing is not None:
            known = self.pricing.list_models()
            if model_name not in known:
                # Warn but still allow — user might be using a new model
                console.print(
                    f"[yellow]  Warning:[/yellow] {model_name!r} not found in pricing registry. "
                    f"Cost tracking will use family-prefix fallback."
                )

        old_model = getattr(self.config.models, agent, "")
        ok = self.orchestrator.set_model(agent, model_name)
        if ok:
            console.print(f"[green]  {agent} model updated:[/green] {old_model} → {model_name}")
        else:
            console.print(f"[red]  Failed to update {agent}[/red]")

    # ------------------------------------------------------------------ #
    #  Display helpers                                                     #
    # ------------------------------------------------------------------ #

    def _print_token_line(self):
        summary = self.token_tracker.get_summary()

        # Per-task delta
        task_str = (
            f"Task: [cyan]{self._last_task_tokens:,}[/cyan] tokens · "
            f"[cyan]${self._last_task_cost:.4f}[/cyan]"
        )
        session_str = (
            f"Session: {summary['total_tokens']:,} tokens · "
            f"${summary['total_cost_usd']:.4f} · "
            f"{summary['percent_used']}% of budget"
        )
        console.print(f"[dim]  {task_str}   {session_str}[/dim]")

        # Soft-limit warning
        if "soft_cap" in summary["warnings"]:
            console.print(
                f"[yellow bold]  Soft limit reached:[/yellow bold] [yellow]"
                f"{summary['total_tokens']:,} tokens / ${summary['total_cost_usd']:.4f} — "
                f"agents will wind down gracefully[/yellow]"
            )
        elif summary["warnings"]:
            console.print(
                f"[yellow bold]  Warning:[/yellow bold] [yellow]"
                f"Token budget at {summary['percent_used']}% "
                f"({summary['total_tokens']:,} / {summary['hard_cap']:,})[/yellow]"
            )

    def _print_banner(self):
        commands = (
            "[bold cyan]Argus[/bold cyan] — Multi-Agent Coding Assistant\n"
            "[dim]Tasks · audit <path> · fix <id> · model · budget · stats · exit[/dim]\n"
            f"[dim]{self._pricing_status}[/dim]"
        )
        left = Panel(commands, border_style="cyan", padding=(0, 2))

        width = console.width or 120
        eye_art = get_eye(width)
        if eye_art:
            try:
                eye_text = Text.from_markup(eye_art)
                console.print(Columns([left, eye_text], expand=True))
                return
            except (UnicodeEncodeError, Exception):
                pass  # fall through to plain banner on terminals that can't render Unicode
        console.print(left)

    def _print_stats(self):
        summary = self.token_tracker.get_summary()

        table = Table(title="Token Usage", border_style="yellow", show_lines=True)
        table.add_column("Agent", style="cyan")
        table.add_column("Tokens", justify="right")
        table.add_column("Cost (USD)", justify="right")
        table.add_column("Calls", justify="right")

        for agent_name, data in summary["per_agent"].items():
            table.add_row(
                agent_name,
                f"{data['tokens']:,}",
                f"${data['cost']:.6f}",
                str(data["calls"]),
            )

        table.add_section()
        table.add_row(
            "[bold]Total[/bold]",
            f"[bold]{summary['total_tokens']:,}[/bold]",
            f"[bold]${summary['total_cost_usd']:.6f}[/bold]",
            "",
        )
        console.print(table)
        console.print(
            f"  Budget: [bold]{summary['percent_used']}%[/bold] token · "
            f"${summary['total_cost_usd']:.4f} of ${summary['dollar_hard_cap']:.2f} dollar cap"
        )

        # Last-task summary
        if self._last_task_tokens:
            console.print(
                f"  Last task: [cyan]{self._last_task_tokens:,}[/cyan] tokens · "
                f"[cyan]${self._last_task_cost:.4f}[/cyan]"
            )

    def _print_response(self, response: str):
        if not response:
            return
        if response.lstrip().startswith("["):
            console.print(f"[yellow]  {response.strip()}[/yellow]")
            return
        if "## Audit Report" in response or "Finding #" in response:
            self._print_severity_bar(response)
        console.print(Markdown(response))

    def _print_severity_bar(self, report: str):
        counts: dict[str, int] = {}
        for sev in _SEV_STYLES:
            m = re.search(rf"(\d+)\s+{sev}", report)
            if m:
                counts[sev] = int(m.group(1))
        if not counts:
            return
        parts = []
        for sev, style in _SEV_STYLES.items():
            n = counts.get(sev, 0)
            if n > 0:
                parts.append(f"[{style}]  {n} {sev}  [/{style}]")
        if parts:
            console.print("".join(parts))
