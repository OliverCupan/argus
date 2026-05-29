"""
Bash Tool — execute shell commands with safety classification.

Commands are classified as SAFE/REVIEW/BLOCKED before execution.
Uses 'bash -c' explicitly (not shell=True) for cross-platform correctness
on Windows (Git Bash) and to avoid shell injection via cmd.exe.
"""

import asyncio
import logging
import shutil

from src.tools.registry import Tool
from src.core.safety import SafetyChecker, SafetyLevel

logger = logging.getLogger(__name__)


def create_bash_tool(config, confirm_callback=None) -> Tool:
    """
    Create and return the bash execution tool.

    Args:
        config: ArgusConfig instance
        confirm_callback: Optional async callable(command: str) -> bool
            Called for REVIEW-level commands. Return True to proceed.
    """
    safety = SafetyChecker(config.safety)
    timeout = config.agent.bash_timeout

    # Resolve bash at creation time — fail fast if missing
    bash_path = shutil.which("bash")
    if bash_path is None:
        raise RuntimeError(
            "bash not found in PATH.\n"
            "  On Windows, install Git for Windows: https://git-scm.com/download/win"
        )
    logger.debug("bash resolved to: %s", bash_path)

    async def handler(command: str) -> str:
        """Execute a bash command after safety classification."""
        level = safety.classify_command(command)
        logger.debug("bash command safety level: %s — %r", level.value, command[:80])

        if level == SafetyLevel.BLOCKED:
            return f"BLOCKED: Command rejected by safety policy: {command}"

        if level == SafetyLevel.REVIEW:
            if confirm_callback is not None:
                approved = await confirm_callback(command)
                if not approved:
                    return f"DENIED: User rejected command: {command}"
            else:
                return (
                    f"REVIEW REQUIRED: This command needs approval before running:\n"
                    f"  {command}\n"
                    f"(No approval mechanism is configured in this session.)"
                )

        try:
            proc = await asyncio.create_subprocess_exec(
                bash_path, "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Error: Command timed out after {timeout}s: {command}"

            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")

            parts = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(f"STDERR:\n{stderr}")
            if proc.returncode != 0:
                parts.append(f"Return code: {proc.returncode}")

            return "\n".join(parts).strip() or "(no output)"

        except Exception as e:
            logger.warning("bash tool exception: %s", e)
            return f"Error: {e}"

    return Tool(
        name="bash",
        description=(
            "Execute a bash command. Use for running tests (pytest), searching code "
            "(grep, find), listing files (ls), checking git status, etc. "
            "Commands run from the project working directory."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                }
            },
            "required": ["command"],
        },
        handler=handler,
    )
