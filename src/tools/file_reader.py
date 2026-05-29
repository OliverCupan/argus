"""
File Reader Tool — read file contents and list directory structure.
"""

import logging
from pathlib import Path

from src.tools.registry import Tool

logger = logging.getLogger(__name__)


def create_file_reader_tool() -> Tool:
    """Create and return the file reader tool."""

    async def handler(
        path: str,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> str:
        """Read a file or list a directory."""
        target = Path(path)

        if not target.exists():
            return f"Error: Path not found: {path}"

        if target.is_dir():
            return _list_directory(target)

        try:
            content = target.read_text(encoding="utf-8")
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except OSError as e:
            return f"Error: Cannot read {path}: {e}"
        except UnicodeDecodeError:
            return f"Error: Cannot read binary file: {path}"

        lines = content.splitlines()

        if line_start is not None:
            start = max(0, line_start - 1)  # 1-indexed → 0-indexed
            end = line_end if line_end is not None else len(lines)
            lines = lines[start:end]
            base = line_start
        else:
            base = 1

        numbered = [f"{i + base:4d} | {line}" for i, line in enumerate(lines)]
        logger.debug("read_file: %s (%d lines)", path, len(numbered))
        return "\n".join(numbered)

    return Tool(
        name="read_file",
        description=(
            "Read a file's contents (with line numbers) or list a directory tree. "
            "Optionally specify line_start/line_end to read a range."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or directory path to read",
                },
                "line_start": {
                    "type": "integer",
                    "description": "Optional: first line to read (1-indexed)",
                },
                "line_end": {
                    "type": "integer",
                    "description": "Optional: last line to read (inclusive)",
                },
            },
            "required": ["path"],
        },
        handler=handler,
    )


def _list_directory(path: Path, max_depth: int = 2) -> str:
    """List directory tree up to max_depth levels."""
    lines: list[str] = []

    def _walk(p: Path, depth: int, prefix: str = "") -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        except PermissionError:
            return

        for entry in entries:
            if entry.name.startswith(".") or entry.name in {"node_modules", "__pycache__"}:
                continue
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                _walk(entry, depth + 1, prefix + "  ")
            else:
                lines.append(f"{prefix}{entry.name}")

    _walk(path, 0)
    return "\n".join(lines) or "(empty directory)"
