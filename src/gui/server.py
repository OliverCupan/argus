"""
FastAPI server for the Argus Web GUI.

Serves the static single-page dashboard and provides:
  GET  /               → index.html
  GET  /static/...     → CSS / JS / assets
  WS   /ws             → real-time event stream
  POST /api/command    → {text: str} run a task
  POST /api/confirm/<id> → {approved: bool}
  GET  /api/stats      → TokenTracker.get_summary()
  GET  /api/config     → models + budget
  POST /api/config/model  → {agent, model}
  POST /api/config/budget → {field, value}
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.gui.event_bus import EventBus, AgentEvent
from src.gui.gui_app import GuiApp

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


# ------------------------------------------------------------------ #
#  Request / response models                                           #
# ------------------------------------------------------------------ #

class CommandRequest(BaseModel):
    text: str

class ConfirmRequest(BaseModel):
    approved: bool

class ModelUpdateRequest(BaseModel):
    agent: str
    model: str

class BudgetUpdateRequest(BaseModel):
    field: str
    value: float

class CompactRequest(BaseModel):
    pass


# ------------------------------------------------------------------ #
#  WebSocket connection manager                                        #
# ------------------------------------------------------------------ #

class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    def get_count(self) -> int:
        """Return the current count of active WebSocket connections."""
        return len(self.active)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        logger.debug("WS connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self.active.remove(ws)
        except ValueError:
            pass
        logger.debug("WS disconnected (%d total)", len(self.active))

    async def broadcast(self, event: AgentEvent) -> None:
        if not self.active:
            return
        msg = json.dumps(event.to_dict(), default=str)
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


# ------------------------------------------------------------------ #
#  Factory                                                             #
# ------------------------------------------------------------------ #

def create_app(gui: GuiApp, event_bus: EventBus) -> FastAPI:
    manager = ConnectionManager()

    async def _emit_connection_update() -> None:
        """Emit a connections_update event to all connected clients."""
        try:
            event_bus.emit_sync("orchestrator", "connections_update", active=manager.get_count())
        except Exception as exc:
            logger.error("Failed to emit connections_update event: %s", exc)

    # Background task: read EventBus queue → broadcast to all WS clients
    _bus_queue: asyncio.Queue[AgentEvent] = event_bus.subscribe()

    async def _broadcast_loop() -> None:
        while True:
            try:
                event = await _bus_queue.get()
                await manager.broadcast(event)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Broadcast error: %s", exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_broadcast_loop())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(title="Argus Web GUI", docs_url=None, redoc_url=None, lifespan=lifespan)

    # ------------------------------------------------------------------ #
    #  Static files                                                        #
    # ------------------------------------------------------------------ #

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = _STATIC_DIR / "index.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ------------------------------------------------------------------ #
    #  WebSocket                                                           #
    # ------------------------------------------------------------------ #

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await manager.connect(ws)
        # Emit connection count update after client is fully added
        await _emit_connection_update()
        # Send current stats immediately on connect
        try:
            await ws.send_text(json.dumps({
                "timestamp": 0,
                "agent_name": "orchestrator",
                "event_type": "token_update",
                "data": {"summary": gui.get_stats()},
            }, default=str))
        except Exception:
            pass
        try:
            while True:
                # Keep connection alive; incoming messages are ignored
                await ws.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(ws)
            await _emit_connection_update()
        except Exception:
            manager.disconnect(ws)
            await _emit_connection_update()

    # ------------------------------------------------------------------ #
    #  REST API                                                            #
    # ------------------------------------------------------------------ #

    @app.post("/api/command")
    async def run_command(req: CommandRequest) -> dict[str, Any]:
        if not req.text.strip():
            raise HTTPException(400, "Empty command")
        result = await gui.handle_command(req.text)
        return {"result": result}

    @app.post("/api/confirm/{request_id}")
    async def confirm_command(request_id: str, req: ConfirmRequest) -> dict[str, Any]:
        ok = gui.resolve_confirm(request_id, req.approved)
        if not ok:
            raise HTTPException(404, f"No pending confirm request: {request_id}")
        return {"ok": True, "approved": req.approved}

    @app.get("/api/stats")
    async def get_stats() -> dict[str, Any]:
        return gui.get_stats()

    @app.get("/api/connections")
    async def get_connections() -> dict[str, Any]:
        return {"active": manager.get_count()}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return gui.get_config()

    @app.post("/api/config/model")
    async def set_model(req: ModelUpdateRequest) -> dict[str, Any]:
        result = gui._handle_model_set(req.agent, req.model)
        return {"result": result}

    @app.post("/api/config/budget")
    async def set_budget(req: BudgetUpdateRequest) -> dict[str, Any]:
        result = gui._handle_budget_set(req.field, str(req.value))
        return {"result": result}

    @app.post("/api/compact")
    async def compact_context() -> dict[str, Any]:
        gui.orchestrator.request_compact()
        await event_bus.emit("orchestrator", "compaction", kind="manual_requested", messages_dropped=0, tokens_saved_est=0)
        return {"ok": True, "message": "Compact requested"}

    return app
