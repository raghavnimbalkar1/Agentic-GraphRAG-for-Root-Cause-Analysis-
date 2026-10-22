"""Explicit opt-in Groq adapter. Importing this module loads no settings or credentials."""

from agent.shadow import DriverReply
from core.llm_usage import token_usage
from langsmith import tracing_context


class GroqShadowDriver:
    kind = "model"

    def __init__(self, model):
        if model.max_retries != 0:
            raise ValueError("Shadow provider retries must be disabled")
        self.model = model

    def complete(self, messages, *, max_output_tokens, timeout_seconds):
        with tracing_context(enabled=False):
            response = self.model.bind(max_tokens=max_output_tokens, timeout=timeout_seconds).invoke(messages)
        return DriverReply(content=response.content, usage=token_usage(response))


def configured_groq_driver():
    """Trusted caller must approve external project-data transfer before using this factory."""
    from core.config import settings

    if settings.llm_provider.value != "groq":
        raise ValueError("The initial shadow adapter supports only the configured Groq provider")
    from agent.nodes.reasoner import _get_llm

    return GroqShadowDriver(_get_llm())
