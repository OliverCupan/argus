"""
Argus — Multi-Agent Coding Assistant

Entry point. Loads config, starts the CLI.
Set ARGUS_LOG_LEVEL=DEBUG for verbose logging.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console

from src.config import load_config
from src.cli import ArgusCliApp

console = Console()


def main():
    # Configure logging from env var (default: WARNING = quiet)
    log_level = os.getenv("ARGUS_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.WARNING),
        format="%(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )

    load_dotenv()

    config_path = Path(__file__).parent / "argus.yaml"
    if not config_path.exists():
        console.print("[red]Error:[/red] argus.yaml not found next to main.py")
        sys.exit(1)

    try:
        config = load_config(config_path)
    except (ValueError, RuntimeError) as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        sys.exit(1)
    except yaml.YAMLError as e:
        console.print(f"[red]Invalid argus.yaml:[/red] {e}")
        sys.exit(1)

    app = ArgusCliApp(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
