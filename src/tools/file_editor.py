"""
File Editor Tool — surgical file edits via search/replace.

old_str must match exactly and appear exactly once in the file.
Validates write path against safety policy before any disk write.
Acquires an advisory write lock via FileLockManager before writing.
"""

import logging
from pathlib import Path

from src.tools.registry import Tool
from src.core.safety import SafetyChecker
from src.core.agent_coordinator import get_coordinator

logger = logging.getLogger(__name__)


def create_file_editor_tool(config=None, agent_name: str = "coder") -> Tool:
    """Create and return the file editor tool."""
    safety = SafetyChecker(config.safety) if config else None
    coordinator = get_coordinator()

    async def handler(path: str, old_str: str, new_str: str) -> str:
        """Replace old_str with new_str in the specified file."""
        if not old_str:
            return "Error: old_str cannot be empty"

        if safety and not safety.validate_file_path(path):
            return f"Error: Writing to '{path}' is blocked by safety policy"

        target = Path(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        if not target.is_file():
            return f"Error: Not a file: {path}"

        try:
            content = target.read_text(encoding="utf-8")
        except PermissionError:
            return f"Error: Permission denied reading: {path}"
        except OSError as e:
            return f"Error: Cannot read {path}: {e}"

        count = content.count(old_str)
        if count == 0:
            return f"Error: old_str not found in {path}"
        if count > 1:
            return (
                f"Error: old_str appears {count} times in {path} — must be unique. "
                f"Add more surrounding context to make it unique."
            )

        new_content = content.replace(old_str, new_str, 1)

        # Acquire write lock with a short timeout
        try:
            async with coordinator.lock_manager.write_lock(path, agent=agent_name, timeout=20.0):
                coordinator.log_access(agent_name, path, "write")
                try:
                    target.write_text(new_content, encoding="utf-8")
                except PermissionError:
                    return f"Error: Permission denied writing: {path}"
                except OSError as e:
                    return f"Error: Cannot write {path}: {e}"
        except TimeoutError as e:
            locked, owner = coordinator.lock_manager.is_write_locked(path)
            return (
                f"Error: Cannot write {path} — file is locked by agent '{owner}'. "
                f"Try again shortly or work on a different file."
            )

        logger.debug("edit_file: %s — replaced %d chars with %d chars", path, len(old_str), len(new_str))
        return (
            f"Successfully edited {path}\n"
            f"--- removed\n{old_str}\n"
            f"+++ added\n{new_str}"
        )

    return Tool(
        name="edit_file",
        description=(
            "Edit a file by replacing an exact string. "
            "old_str must appear exactly once in the file — include enough surrounding "
            "context to make it unique. new_str is the replacement."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_str": {
                    "type": "string",
                    "description": "Exact string to find — must appear exactly once in the file",
                },
                "new_str": {
                    "type": "string",
                    "description": "Replacement string",
                },
            },
            "required": ["path", "old_str", "new_str"],
        },
        handler=handler,
    )
