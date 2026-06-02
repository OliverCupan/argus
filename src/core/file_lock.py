"""
File Lock Manager — async advisory locks for agent coordination.

Prevents write conflicts when multiple agents could theoretically access
the same files. Locks are in-process (not OS-level), advisory, and
released automatically on error via async context managers.

Usage:
    lock_mgr = FileLockManager()

    # Acquire a write lock with timeout
    async with lock_mgr.write_lock("src/foo.py", agent="coder"):
        # safe to write
        ...

    # Read locks allow concurrent access (multiple readers, one writer)
    async with lock_mgr.read_lock("src/foo.py", agent="explorer"):
        content = ...
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)


class _FileLockState:
    """Internal state for a single file's locking."""
    def __init__(self):
        self._write_lock = asyncio.Lock()
        self._readers: int = 0
        self._readers_lock = asyncio.Lock()
        self._write_owner: Optional[str] = None
        self._read_owners: list[str] = []

    @property
    def write_owner(self) -> Optional[str]:
        return self._write_owner

    @property
    def reader_count(self) -> int:
        return self._readers


class FileLockManager:
    """
    Async advisory file lock manager for agent coordination.

    Implements readers-writers lock semantics:
    - Multiple agents can hold read locks concurrently.
    - Only one agent can hold the write lock; during writes, no reads allowed.
    """

    def __init__(self):
        self._states: dict[str, _FileLockState] = {}
        self._global_lock = asyncio.Lock()

    def _get_state(self, path: str) -> _FileLockState:
        if path not in self._states:
            self._states[path] = _FileLockState()
        return self._states[path]

    def is_write_locked(self, path: str) -> tuple[bool, Optional[str]]:
        """Return (is_write_locked, owner_agent_name)."""
        state = self._states.get(path)
        if state is None:
            return False, None
        return state._write_lock.locked(), state.write_owner

    def is_read_locked(self, path: str) -> tuple[bool, list[str]]:
        """Return (has_readers, [reader_agent_names])."""
        state = self._states.get(path)
        if state is None:
            return False, []
        return state._readers > 0, list(state._read_owners)

    async def acquire_write(self, path: str, agent: str, timeout: float = 30.0) -> bool:
        """
        Acquire a write lock on path. Returns True on success, False on timeout.
        Blocks until all current readers finish (readers-writers protocol).
        """
        state = self._get_state(path)
        try:
            await asyncio.wait_for(state._write_lock.acquire(), timeout=timeout)
            state._write_owner = agent
            logger.debug("Write lock acquired: %s by %s", path, agent)
            return True
        except asyncio.TimeoutError:
            logger.warning("Write lock timeout: %s (agent=%s, held by %s)", path, agent, state.write_owner)
            return False

    async def release_write(self, path: str, agent: str) -> None:
        """Release a write lock."""
        state = self._states.get(path)
        if state is None or not state._write_lock.locked():
            logger.warning("release_write: no lock held on %s by %s", path, agent)
            return
        state._write_owner = None
        state._write_lock.release()
        logger.debug("Write lock released: %s by %s", path, agent)

    async def acquire_read(self, path: str, agent: str, timeout: float = 30.0) -> bool:
        """
        Acquire a read lock. Multiple agents can hold read locks simultaneously.
        Blocked only if a write lock is held (or being acquired).
        """
        state = self._get_state(path)
        try:
            # Wait for any active write lock to be released
            await asyncio.wait_for(_wait_for_unlock(state._write_lock), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Read lock timeout: %s (agent=%s)", path, agent)
            return False

        async with state._readers_lock:
            state._readers += 1
            state._read_owners.append(agent)
        logger.debug("Read lock acquired: %s by %s (%d readers)", path, agent, state._readers)
        return True

    async def release_read(self, path: str, agent: str) -> None:
        """Release a read lock."""
        state = self._states.get(path)
        if state is None or state._readers <= 0:
            return
        async with state._readers_lock:
            state._readers = max(0, state._readers - 1)
            if agent in state._read_owners:
                state._read_owners.remove(agent)
        logger.debug("Read lock released: %s by %s (%d readers left)", path, agent, state._readers)

    # ------------------------------------------------------------------ #
    #  Async context managers (preferred API)                              #
    # ------------------------------------------------------------------ #

    @asynccontextmanager
    async def write_lock(self, path: str, agent: str, timeout: float = 30.0):
        """Context manager for write-exclusive access."""
        acquired = await self.acquire_write(path, agent, timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire write lock on {path!r} within {timeout}s "
                f"(held by {self.is_write_locked(path)[1]!r})"
            )
        try:
            yield
        finally:
            await self.release_write(path, agent)

    @asynccontextmanager
    async def read_lock(self, path: str, agent: str, timeout: float = 30.0):
        """Context manager for shared read access."""
        acquired = await self.acquire_read(path, agent, timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire read lock on {path!r} within {timeout}s"
            )
        try:
            yield
        finally:
            await self.release_read(path, agent)

    def status(self) -> dict:
        """Return current lock state for all files (for debugging)."""
        result = {}
        for path, state in self._states.items():
            if state._write_lock.locked() or state._readers > 0:
                result[path] = {
                    "write_owner": state.write_owner,
                    "readers": state._readers,
                    "read_owners": list(state._read_owners),
                }
        return result


async def _wait_for_unlock(lock: asyncio.Lock) -> None:
    """Wait until a lock is not held (non-acquiring wait)."""
    if not lock.locked():
        return
    # Acquire and immediately release — this waits for the lock to free
    async with lock:
        pass
