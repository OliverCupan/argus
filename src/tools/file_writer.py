"""
File Writer Tool — create new files on disk.
Validates write path against safety policy before any disk write.
"""

import logging
from pathlib import Path

from src.tools.registry import Tool
from src.core.safety import SafetyChecker

logger = logging.getLogger(__name__)


def create_file_writer_tool(config=None) -> Tool:
    """Create and return the file writer tool."""
    safety = SafetyChecker(config.safety) if config else None

    async def handler(path: str, content: str, overwrite: bool = False) -> str:
        """Create a new file with the given content."""
        # Safety: validate write path
        if safety and not safety.validate_file_path(path):
            return f"Error: Writing to '{path}' is blocked by safety policy"

        target = Path(path)
        if target.exists() and not overwrite:
            return (
                f"Error: File already exists: {path}\n"
                f"  Set overwrite=true to replace it."
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except PermissionError:
            return f"Error: Permission denied writing: {path}"
        except OSError as e:
            return f"Error: Cannot write {path}: {e}"

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
