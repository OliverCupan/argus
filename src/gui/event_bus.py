"""
Async event bus for the Argus Web GUI.

All agents, tools, and the orchestrator publish AgentEvent objects here.
The WebSocket manager subscribes and fans out to all connected browsers.

Event types (string constants):
  AGENT_STARTED      agent began a run
  AGENT_ITERATION    each ReAct loop iteration
  TOOL_CALL          agent is about to call a tool
  TOOL_RESULT        tool result received
  AGENT_TEXT         LLM produced text content this iteration
  AGENT_FINISHED     agent run completed
  STATUS_UPDATE      orchestrator phase transition
  TOKEN_UPDATE       token/cost stats snapshot
  CONFIRM_REQUIRED   bash tool needs user approval
  TASK_COMPLETE      full coding/audit task finished
  CONNECTIONS_UPDATE WebSocket client connection count changed
"""

import asyncio
import time
from dataclasses import dataclass, field, asdict

# ------------------------------------------------------------------ #
#  Event type constants                                                #
# ------------------------------------------------------------------ #

AGENT_STARTED      = "agent_started"
AGENT_ITERATION    = "agent_iteration"
TOOL_CALL          = "tool_call"
TOOL_RESULT        = "tool_result"
AGENT_TEXT         = "agent_text"
AGENT_FINISHED     = "agent_finished"
STATUS_UPDATE      = "status_update"
TOKEN_UPDATE       = "token_update"
CONFIRM_REQUIRED   = "confirm_required"
TASK_COMPLETE      = "task_complete"
CONNECTIONS_UPDATE = "connections_update"
COMPACTION       = "compaction"


@dataclass
class AgentEvent:
    """A single event emitted during agent execution."""
    timestamp: float
    agent_name: str
    event_type: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class EventBus:
    """
    Simple async pub/sub.

    Subscribers receive a private asyncio.Queue.  Each emit() call
    puts a copy of the event into every subscriber's queue without
    blocking the caller.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    # ------------------------------------------------------------------ #
    #  Subscription                                                        #
    # ------------------------------------------------------------------ #

    def subscribe(self) -> asyncio.Queue:
        """Return a new queue that will receive all future events."""
        q: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    # ------------------------------------------------------------------ #
    #  Publishing                                                          #
    # ------------------------------------------------------------------ #

    def _put(self, agent_name: str, event_type: str, **data) -> None:
        """Create an event and put it into every subscriber queue."""
        event = AgentEvent(
            timestamp=time.time(),
            agent_name=agent_name,
            event_type=event_type,
            data=data,
        )
        for q in self._queues:
            q.put_nowait(event)

    async def emit(self, agent_name: str, event_type: str, **data) -> None:
        """Create and fan-out an event to all subscribers (async-compatible)."""
        self._put(agent_name, event_type, **data)

    # Convenience synchronous variant for fire-and-forget callers that
    # are not themselves async (e.g. a sync callback).
    def emit_sync(self, agent_name: str, event_type: str, **data) -> None:
        """Fire an event without awaiting (puts into queues directly)."""
        self._put(agent_name, event_type, **data)
