"""Provider factory: the only place that decides fake vs live."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from incident_commander.config import Settings
from incident_commander.models.fake import FakeChatModel


def get_chat_model(settings: Settings, *, compromised: bool = False) -> BaseChatModel:
    if settings.mode == "mock":
        return FakeChatModel(compromised=compromised)
    if compromised:
        raise ValueError("compromised mode exists only for the fake model")
    # Imported lazily so mock mode never needs the Anthropic SDK configured.
    from langchain_anthropic import ChatAnthropic

    # The key is read from ANTHROPIC_API_KEY by the SDK; we never pass or store it.
    return ChatAnthropic(model_name=settings.anthropic_model, max_tokens_to_sample=4096, timeout=120, stop=None)
