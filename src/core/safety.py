"""
Safety — tool call classification.

Classifies bash commands as SAFE / REVIEW / BLOCKED.
Validates file write paths against allowed directories.
"""

import re
from enum import Enum
from pathlib import Path

from src.config import SafetyConfig


# Files the agent should never be allowed to overwrite
SENSITIVE_FILES = {".env", ".env.local", ".env.production", ".gitconfig"}
SENSITIVE_DIRS = {".git", ".ssh"}


class SafetyLevel(Enum):
    SAFE = "safe"         # execute immediately
    REVIEW = "review"     # requires user confirmation
    BLOCKED = "blocked"   # rejected, never executed


class SafetyChecker:
    def __init__(self, config: SafetyConfig):
        self.config = config

    def classify_command(self, command: str) -> SafetyLevel:
        """
        Classify a bash command by safety level.

        Returns:
            SafetyLevel enum value
        """
        # Check blocked commands first (word-boundary regex — prevents false
        # positives where a blocked token like "rm" appears inside a safe word
        # such as "chmod" or "form").
        for blocked in self.config.blocked_commands:
            if re.search(r"\b" + re.escape(blocked), command):
                return SafetyLevel.BLOCKED

        # Check review patterns
        for pattern in self.config.review_patterns:
            if re.search(r"\b" + re.escape(pattern), command):
                return SafetyLevel.REVIEW

        return SafetyLevel.SAFE

    def validate_file_path(self, path: str) -> bool:
        """
        Check if a file path is within allowed write directories.

        Returns:
            True if write is allowed, False if blocked.
        """
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return False

        # Block writes to sensitive files regardless of directory
        if resolved.name in SENSITIVE_FILES:
            return False

        # Block writes inside sensitive directories (e.g. .git/)
        for part in resolved.parts:
            if part in SENSITIVE_DIRS:
                return False

        # Check containment within allowed_write_paths
        for allowed in self.config.allowed_write_paths:
            try:
                allowed_abs = Path(allowed).resolve()
                # Path is allowed if it equals or is inside an allowed dir
                resolved.relative_to(allowed_abs)
                return True
            except ValueError:
                continue

        return False
