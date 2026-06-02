"""
File Writer Tool — create new files on disk.
Validates write path against safety policy before any disk write.
Acquires an advisory write lock via FileLockManager before writing.
"""

import logging
from pathlib import Path

from src.tools.registry import Tool
from src.core.safety import SafetyChecker
from src.core.agent_coordinator import get_coordinator

logger = logging.getLogger(__name__)


def create_file_writer_tool(config=None, agent_name: str = "coder") -> Tool:
    """Create and return the file writer tool."""
    safety = SafetyChecker(config.safety) if config else None
    coordinator = get_coordinator()

    async def handler(path: str, content: str, overwrite: bool = False) -> str:
        """Create a new file with the given content."""
        if safety and not safety.validate_file_path(path):
            return f"Error: Writing to '{path}' is blocked by safety policy"

        target = Path(path)
        if target.exists() and not overwrite:
            return (
                f"Error: File already exists: {path}\n"
                f"  Set overwrite=true to replace it."
            )

        try:
            async with coordinator.lock_manager.write_lock(path, agent=agent_name, timeout=20.0):
                coordinator.log_access(agent_name, path, "write")
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                except PermissionError:
                    return f"Error: Permission denied writing: {path}"
                except OSError as e:
                    return f"Error: Cannot write {path}: {e}"
        except TimeoutError:
            locked, owner = coordinator.lock_manager.is_write_locked(path)
            return (
                f"Error: Cannot write {path} — file is locked by agent '{owner}'. "
                f"Try again shortly or work on a different file."
            )

        logger.debug("write_file: created %s (%d chars)", path, len(content))
        return f"Created {path} ({len(content)} chars)"

    return Tool(
        name="write_file",
        description=(
            "Create a new file with the specified content. "
            "Will not overwrite existing files unless overwrite=true."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path for the new file",
                },
                "content": {
                    "type": "string",
                    "description": "File content to write",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Allow overwriting existing files (default: false)",
                },
            },
            "required": ["path", "content"],
        },
        handler=handler,
    )
