"""
CLI interface for Argus.

Phase 1: single Coder agent  — free-form tasks
Phase 2: Orchestrator routes  — 'audit <path>' or free-form coding tasks
Phase 4: fix <finding_id>, colored severity output, polished UI
"""

import asyncio
import itertools
import os
import re
import sys
from typing import Coroutine, Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from src.config import ArgusConfig
from src.core.token_tracker import TokenTracker
from src.agents.orchestrator import Orchestrator

console = Console()

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_SPINNER_WIDTH = 80  # characters to pad/clear per line

_SEV_STYLES = {
    "CRITICAL": "bold red",
    "HIGH":     "bold yellow",
    "MEDIUM":   "bold cyan",
    "LOW":      "dim",
}


class ArgusCliApp:
    def __init__(self, config: ArgusConfig):
        self.config = config
        self.token_tracker = TokenTracker(config.token_budget)
        self._current_status = "Working…"
        self._spinner_paused = False

        def _clear_spinner_line() -> None:
            sys.stdout.write("\r" + " " * _SPINNER_WIDTH + "\r")
            sys.stdout.flush()

        # REVIEW confirmation callback — pauses the spinner, prompts, resumes.
        async def confirm_callback(command: str) -> bool:
            self._spinner_paused = True
            await asyncio.sleep(0.15)   # let spinner task see the pause flag
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
        """Main input loop."""
        self._print_banner()
        try:
            await self._input_loop()
        finally:
            await self.orchestrator.close()

    async def _run_agent(self, coro: Coroutine[Any, Any, str]) -> str | None:
        """
        Run an agent coroutine with an inline spinner.
        Returns the result string, or None if interrupted.
        """
        self._current_status = "Starting…"

        async def _spinner_task() -> None:
            frames = itertools.cycle(_SPINNER_FRAMES)
            while True:
                if not self._spinner_paused:
                    frame = next(frames)
                    line = f"\r  {frame}  {self._current_status}"
                    sys.stdout.write(line.ljust(_SPINNER_WIDTH))
                    sys.stdout.flush()
                await asyncio.sleep(0.12)

        orig_status = self.orchestrator._status

        async def live_status(message: str) -> None:
            self._current_status = message

        self.orchestrator._status = live_status
        spinner = asyncio.create_task(_spinner_task())
        try:
            result = await coro
            return result
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            return None
        finally:
            spinner.cancel()
            self.orchestrator._status = orig_status
            sys.stdout.write("\r" + " " * _SPINNER_WIDTH + "\r")
            sys.stdout.flush()

    async def _input_loop(self):
        while True:
            try:
                user_input = console.input("\n[bold cyan]argus >[/bold cyan] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Goodbye.[/dim]")
                return

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                console.print("[dim]Goodbye.[/dim]")
                return
            if user_input.lower() == "stats":
                self._print_stats()
                continue

            # fix <id> — apply a fix for a specific finding from the last audit
            fix_match = re.match(r"^fix\s+(\d+)\s*$", user_input, re.IGNORECASE)
            if fix_match:
                finding_id = int(fix_match.group(1))
                result_text = await self._run_agent(
                    self.orchestrator.fix_finding(finding_id)
                )
                if result_text:
                    self._print_response(result_text)
                    self._print_token_line()
                continue

            # Inject cwd for relative path awareness
            cwd = os.getcwd()
            augmented = f"[Working directory: {cwd}]\n\n{user_input}"

            result_text = await self._run_agent(self.orchestrator.handle(augmented))
            if result_text is None:
                continue

            self._print_response(result_text)
            self._print_token_line()

    def _print_token_line(self):
        summary = self.token_tracker.get_summary()
        console.print(
            f"[dim]  {summary['total_tokens']:,} tokens · "
            f"${summary['total_cost_usd']:.4f} · "
            f"{summary['percent_used']}% of budget[/dim]"
        )
        if summary["warnings"]:
            console.print(
                f"[yellow bold]  Warning:[/yellow bold] [yellow]"
                f"Token budget at {summary['percent_used']}% "
                f"({summary['total_tokens']:,} / {summary['hard_cap']:,})[/yellow]"
            )

    def _print_banner(self):
        console.print(Panel(
            "[bold cyan]Argus[/bold cyan] — Multi-Agent Coding Assistant\n"
            "[dim]Commands: free-form tasks · "
            "'audit <path>' · 'fix <id>' · 'stats' · 'exit'[/dim]",
            border_style="cyan",
            padding=(0, 2),
        ))

    def _print_stats(self):
        """Display current token usage as a Rich table."""
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
                f"${data['cost']:.4f}",
                str(data["calls"]),
            )

        table.add_section()
        table.add_row(
            "[bold]Total[/bold]",
            f"[bold]{summary['total_tokens']:,}[/bold]",
            f"[bold]${summary['total_cost_usd']:.4f}[/bold]",
            "",
        )

        console.print(table)
        console.print(
            f"Budget: [bold]{summary['percent_used']}%[/bold] used "
            f"({summary['total_tokens']:,} / {summary['hard_cap']:,} tokens)"
        )

    def _print_response(self, response: str):
        """Display agent response as formatted markdown.
        Budget/API error messages are shown as warnings.
        Audit reports get an extra colored severity summary bar on top.
        """
        if not response:
            return
        # Agent-level error / budget messages (start with '[')
        if response.lstrip().startswith("["):
            console.print(f"[yellow]  {response.strip()}[/yellow]")
            return
        if "## Audit Report" in response or "Finding #" in response:
            self._print_severity_bar(response)
        console.print(Markdown(response))

    def _print_severity_bar(self, report: str):
        """Print a one-line colored severity count bar above an audit report."""
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
