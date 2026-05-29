"""
CLI interface for Argus.

Phase 1: Single Coder agent with all four tools.
"""

import os

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table

from src.config import ArgusConfig
from src.core.llm_client import LLMClient
from src.core.token_tracker import TokenTracker
from src.tools.registry import build_registry
from src.agents.coder import Coder

console = Console()


class ArgusCliApp:
    def __init__(self, config: ArgusConfig):
        self.config = config
        self.token_tracker = TokenTracker(config.token_budget)

        # REVIEW confirmation callback — prompts inline, runs in the event loop
        async def confirm_callback(command: str) -> bool:
            console.print(
                f"\n[yellow bold]⚠  REVIEW REQUIRED[/yellow bold]\n"
                f"  The agent wants to run:\n"
                f"  [bold]{command}[/bold]"
            )
            answer = console.input("  Allow? [y/N] ").strip().lower()
            return answer in ("y", "yes")

        self.llm = LLMClient(config)
        self.tools = build_registry(config, confirm_callback=confirm_callback)
        self.coder = Coder(config, self.llm, self.token_tracker, self.tools)

    async def run(self):
        """Main input loop."""
        self._print_banner()

        try:
            await self._input_loop()
        finally:
            # Clean up the httpx client to avoid ResourceWarning
            await self.llm.close()

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

            # Inject working directory so the agent knows where relative paths resolve
            cwd = os.getcwd()
            augmented = f"[Working directory: {cwd}]\n\n{user_input}"

            console.print("[dim]Agent working…[/dim]")
            try:
                result = await self.coder.run(augmented)
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
                continue

            self._print_response(result.content)

            # Brief per-response stats
            total_tokens = result.total_input_tokens + result.total_output_tokens
            console.print(
                f"[dim]  {result.iterations} iteration(s) · "
                f"{total_tokens:,} tokens this request[/dim]"
            )

            # Surface any budget warnings
            summary = self.token_tracker.get_summary()
            if summary["warnings"]:
                console.print(
                    f"[yellow bold]Warning:[/yellow bold] "
                    f"[yellow]Token budget at {summary['percent_used']}% "
                    f"({summary['total_tokens']:,} / {summary['hard_cap']:,})[/yellow]"
                )

    def _print_banner(self):
        console.print(Panel(
            "[bold cyan]Argus[/bold cyan] — Coding Assistant  [dim](Phase 1)[/dim]\n"
            "[dim]Commands: free-form tasks, 'stats', 'exit'[/dim]",
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
