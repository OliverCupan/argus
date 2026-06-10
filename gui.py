"""
Argus — Web GUI entry point.

Starts a FastAPI/uvicorn server at http://127.0.0.1:7777 and opens
the dashboard in the default browser.

Usage:
    python gui.py
    python gui.py --port 8080 --no-browser
"""

# Force UTF-8 I/O on Windows before any other imports.
import os
os.environ.setdefault("PYTHONUTF8", "1")

import argparse
import asyncio
import logging
import sys
import webbrowser
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv

from src.config import load_config
from src.core.pricing import ModelPricing
from src.gui.event_bus import EventBus
from src.gui.gui_app import GuiApp
from src.gui.server import create_app


async def _start(host: str, port: int, open_browser: bool) -> None:
    log_level = os.getenv("ARGUS_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.WARNING),
        format="%(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )

    load_dotenv()

    config_path = Path(__file__).parent / "argus.yaml"
    if not config_path.exists():
        print("Error: argus.yaml not found next to gui.py", file=sys.stderr)
        sys.exit(1)

    try:
        config = load_config(config_path)
    except (ValueError, RuntimeError, yaml.YAMLError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # Fetch live pricing (non-fatal if offline)
    pricing = ModelPricing()
    try:
        await pricing.fetch_prices()
    except Exception as exc:
        logging.getLogger(__name__).warning("Pricing fetch failed: %s", exc)

    event_bus = EventBus()
    gui       = GuiApp(config, pricing=pricing, event_bus=event_bus)
    app       = create_app(gui, event_bus)

    url = f"http://{host}:{port}"
    print(f"\n  Argus Web GUI  ->  {url}\n  Press Ctrl+C to stop.\n")

    _browser_task = None
    if open_browser:
        # Small delay so the server has time to bind before the browser hits it
        async def _open() -> None:
            await asyncio.sleep(1.2)
            webbrowser.open(url)
        _browser_task = asyncio.create_task(_open())

    cfg    = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Argus Web GUI")
    parser.add_argument("--host",       default="127.0.0.1")
    parser.add_argument("--port",       type=int, default=7777)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    asyncio.run(_start(args.host, args.port, not args.no_browser))


if __name__ == "__main__":
    main()
