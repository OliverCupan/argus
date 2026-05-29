"""
CLI interface for Argus.

Phase 1: single Coder agent  — free-form tasks
Phase 2: Orchestrator routes  — 'audit <path>' or free-form coding tasks
"""

import asyncio
import itertools
import os
import sys

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

            # Inject cwd for relative path awareness (passed to orchestrator,
            # which injects it into the first user message via Coder)
            cwd = os.getcwd()
            augmented = f"[Working directory: {cwd}]\n\n{user_input}"

            # Run the orchestrator with an inline spinner.
            # Uses \r to overwrite one line — works in all terminals including Git Bash.
            result_text = ""
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
                result_text = await self.orchestrator.handle(augmented)
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
                continue
            finally:
                spinner.cancel()
                self.orchestrator._status = orig_status
                # Clear the spinner line before printing the response
                sys.stdout.write("\r" + " " * _SPINNER_WIDTH + "\r")
                sys.stdout.flush()

            self._print_response(result_text)

            # Brief per-request token summary
            summary = self.token_tracker.get_summary()
            console.print(
                f"[dim]  Total session: {summary['total_tokens']:,} tokens · "
                f"${summary['total_cost_usd']:.4f} · "
                f"{summary['percent_used']}% of budget[/dim]"
            )

            # Surface budget warnings
            if summary["warnings"]:
                console.print(
                    f"[yellow bold]Warning:[/yellow bold] [yellow]"
                    f"Token budget at {summary['percent_used']}% "
                    f"({summary['total_tokens']:,} / {summary['hard_cap']:,})[/yellow]"
                )

    def _print_banner(self):
        console.print(Panel(
            "[bold cyan]Argus[/bold cyan] — Multi-Agent Coding Assistant\n"
            "[dim]Commands: free-form tasks · 'audit <path>' · 'stats' · 'exit'[/dim]",
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
        """Display agent response as formatted markdown."""
        if response:
            console.print(Markdown(response))
