from app.prompts.prompt_manager import PromptManager
from app.models.generation_policy import mark_incomplete


class BaseAgent:

    def __init__(
        self,
        agent_id,
        name,
        role,
        model
    ):

        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.model = model

        self.prompt_manager = PromptManager()

    def build_prompt(
        self,
        task,
        context=None
    ):

        prompt = self.prompt_manager.build_prompt(
            role=self.role,
            task=task,
            context=context
        )

        return f"""
{prompt}

================================
Agent Information
================================

Agent Name:
{self.name}

Agent ID:
{self.agent_id}
""".strip()

    def execute(
        self,
        task,
        model_adapter,
        context=None
    ):

        prompt = self.build_prompt(
            task=task,
            context=context
        )

        response = model_adapter.generate(
            prompt
        )

        return mark_incomplete(response, model_adapter)
