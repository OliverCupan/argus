"""
Explorer Agent — codebase scout.

Maps project structure, reads relevant files, produces a compact summary.
Uses Haiku for cost efficiency (this is mostly tool-call work).
"""

from src.core.agent_loop import BaseAgent


class Explorer(BaseAgent):
    name = "explorer"

    system_prompt = """You are Explorer, a codebase scout agent. Your job is to quickly map
a project's structure and identify the files relevant to the current task.

Your workflow:
1. List the project directory structure
2. Read key files (entry points, configs, the files mentioned in the task)
3. Produce a COMPACT summary including:
   - Project type and structure
   - Key files and their purposes
   - Relevant code sections for the task at hand

Keep your summary SHORT and focused. Other agents will use this as context,
so every unnecessary line wastes their context window.

Do NOT suggest fixes or changes. Just report what you find."""

    def get_model(self) -> str:
        return self.config.models.explorer

    def get_tool_names(self) -> list[str]:
        return ["read_file", "bash"]
