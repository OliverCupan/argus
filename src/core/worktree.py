"""
Worktree Manager — git worktree isolation for the Coder agent.

When config.agent.use_worktrees is True, the Coder gets its own git
worktree at .argus/worktrees/coder/ before starting work. After the
Coder finishes, changes are merged back to the main working tree via
a git diff + apply approach (avoids complex 3-way merges for the typical
single-agent-writes-at-a-time use case).

Auditors and read-only agents always work on the main tree.

Usage:
    wm = WorktreeManager(config)
    path = await wm.create("coder")
    # ... Coder runs in `path` ...
    summary = await wm.merge_back("coder")
    await wm.cleanup("coder")
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from src.config import AgentConfig

logger = logging.getLogger(__name__)


class WorktreeManager:
    """
    Manages git worktrees for agent isolation.

    Only supported when the project root is a git repository.
    Falls back gracefully when git is not available or repo is not initialized.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._worktree_root = Path(config.worktree_dir)
        self._active: dict[str, Path] = {}   # agent_name → worktree path

    async def is_git_repo(self) -> bool:
        """Check if the current directory is inside a git repository."""
        rc, _, _ = await _run("git", "rev-parse", "--is-inside-work-tree")
        return rc == 0

    async def create(self, agent_name: str, base_branch: str = "HEAD") -> Optional[Path]:
        """
        Create a git worktree for agent_name at worktree_dir/agent_name/.
        Returns the worktree path, or None if creation failed.
        """
        if not await self.is_git_repo():
            logger.warning("WorktreeManager: not a git repo — worktrees disabled")
            return None

        worktree_path = self._worktree_root / agent_name
        branch_name = f"argus/{agent_name}"

        # Remove stale worktree if it exists
        if worktree_path.exists():
            await self.cleanup(agent_name)

        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        rc, stdout, stderr = await _run(
            "git", "worktree", "add",
            "-b", branch_name,
            str(worktree_path),
            base_branch,
        )
        if rc != 0:
            logger.error("Failed to create worktree for %s: %s", agent_name, stderr)
            return None

        self._active[agent_name] = worktree_path
        logger.info("Worktree created: %s → %s", agent_name, worktree_path)
        return worktree_path

    async def merge_back(self, agent_name: str, main_dir: str = ".") -> str:
        """
        Merge changes from the agent's worktree back to the main working tree.
        Uses git diff + apply for a clean, conflict-aware merge.
        Returns a summary string of what changed.
        """
        wt_path = self._active.get(agent_name)
        if wt_path is None or not wt_path.exists():
            return f"No active worktree for {agent_name}"

        # Get the diff from the worktree branch relative to the original commit
        rc, diff, stderr = await _run(
            "git", "-C", str(wt_path),
            "diff", "HEAD~1", "HEAD",  # diff of the Coder's commits
        )
        if rc != 0:
            logger.error("Failed to get diff for %s: %s", agent_name, stderr)
            return f"Could not get diff: {stderr}"

        if not diff.strip():
            return f"No changes in worktree for {agent_name}"

        # Apply the patch to the main tree
        rc, _, stderr = await _run(
            "git", "apply", "--3way",
            stdin=diff.encode(),
        )
        if rc != 0:
            logger.warning("Merge conflict applying %s worktree: %s", agent_name, stderr)
            # Fall back: copy files directly
            return await self._fallback_copy(agent_name, wt_path, main_dir)

        # Count changed files
        changed = diff.count("\ndiff --git")
        logger.info("Merged worktree for %s: %d file(s) changed", agent_name, changed)
        return f"Merged {changed} file change(s) from {agent_name} worktree"

    async def _fallback_copy(self, agent_name: str, wt_path: Path, main_dir: str) -> str:
        """
        Fallback merge: copy changed files directly from worktree to main tree.
        Used when git apply fails (e.g., no common ancestor).
        """
        rc, changed_files_out, _ = await _run(
            "git", "-C", str(wt_path), "diff", "--name-only", "HEAD~1", "HEAD"
        )
        if rc != 0 or not changed_files_out.strip():
            return "Fallback merge: no changed files found"

        copied = 0
        for rel_path in changed_files_out.strip().splitlines():
            src = wt_path / rel_path
            dst = Path(main_dir) / rel_path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1

        return f"Fallback merge: copied {copied} file(s) from {agent_name} worktree"

    async def diff(self, agent_name: str) -> str:
        """Show what changed in this worktree compared to HEAD."""
        wt_path = self._active.get(agent_name)
        if wt_path is None:
            return f"No active worktree for {agent_name}"
        rc, diff, _ = await _run("git", "-C", str(wt_path), "diff", "HEAD")
        return diff or "(no uncommitted changes)"

    async def cleanup(self, agent_name: str) -> None:
        """Remove the worktree and its associated branch."""
        wt_path = self._worktree_root / agent_name
        branch_name = f"argus/{agent_name}"

        if wt_path.exists():
            rc, _, stderr = await _run("git", "worktree", "remove", "--force", str(wt_path))
            if rc != 0:
                logger.warning("git worktree remove failed: %s — forcing rmdir", stderr)
                shutil.rmtree(wt_path, ignore_errors=True)

        # Delete the temp branch (ignore errors if it doesn't exist)
        await _run("git", "branch", "-D", branch_name)

        self._active.pop(agent_name, None)
        logger.info("Worktree cleaned up: %s", agent_name)

    async def cleanup_all(self) -> None:
        for agent_name in list(self._active.keys()):
            await self.cleanup(agent_name)


async def _run(*args: str, stdin: bytes = b"") -> tuple[int, str, str]:
    """Run a git subprocess, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin if stdin else None),
            timeout=60.0,
        )
        return proc.returncode or 0, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")
    except asyncio.TimeoutError:
        return 1, "", "timeout"
    except FileNotFoundError:
        return 1, "", "git not found"
