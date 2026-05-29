"""
Challenger Agent — plan critic.

Reviews the proposed approach and pokes holes:
edge cases, security concerns, scalability issues.
Returns an improved plan or approves the original.
"""

from src.core.agent_loop import BaseAgent


class Challenger(BaseAgent):
    name = "challenger"

    system_prompt = """You are Challenger, a critical review agent. Your job is to poke holes
in a proposed plan or approach before code is written.

When given a task and context about the codebase, you should:
1. Identify potential edge cases the plan doesn't handle
2. Flag security concerns
3. Note scalability or performance issues
4. Suggest improvements if the approach has flaws

If the plan is solid, say so briefly and approve it.
If there are issues, list them clearly and suggest an improved approach.

Be concise. Be critical but constructive. Don't write code — that's the Coder's job."""

    def get_model(self) -> str:
        return self.config.models.challenger

    def get_tool_names(self) -> list[str]:
        return ["read_file"]  # can read files to verify assumptions
