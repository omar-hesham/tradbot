from typing import Optional
from sqlalchemy import select
from core.database import get_session
from core.models import BotConfig
from ai_brain.base_provider import BaseAIProvider
from ai_brain.ollama_provider import OllamaProvider
from ai_brain.openai_provider import OpenAIProvider
from ai_brain.anthropic_provider import AnthropicProvider
from ai_brain.opencode_provider import OpenCodeProvider
from ai_brain.openrouter_provider import OpenRouterProvider
from ai_brain.openclaw_provider import OpenclawProvider
from ai_brain.codex_cli_provider import CodexCliProvider


class AINotConfiguredError(Exception):
    pass


PROVIDERS = {
    "openclaw": OpenclawProvider,
    "codex": CodexCliProvider,
    "ollama": OllamaProvider,
    "opencode": OpenCodeProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "openrouter": OpenRouterProvider,
}


async def get_config_value(key: str) -> Optional[str]:
    async for session in get_session():
        result = await session.execute(select(BotConfig).where(BotConfig.key == key))
        config = result.scalar_one_or_none()
        return config.value if config else None


async def is_provider_enabled(provider_name: str) -> bool:
    value = await get_config_value(f"ai_provider_enabled_{provider_name}")
    return value != "false"


async def get_ai_provider() -> BaseAIProvider:
    provider_name = await get_config_value("ai_provider")
    if not provider_name:
        raise AINotConfiguredError("AI provider not configured")
    if not await is_provider_enabled(provider_name):
        raise AINotConfiguredError(f"AI provider '{provider_name}' is switched off")

    model = await get_config_value("ai_model")

    provider_class = PROVIDERS.get(provider_name)
    if not provider_class:
        raise AINotConfiguredError(f"Unknown provider: {provider_name}")

    return provider_class(model=model)


def get_available_providers() -> list[dict]:
    return [
        {"name": "openclaw", "display": "Openclaw Gateway", "needs_key": False},
        {"name": "codex",    "display": "Codex CLI",         "needs_key": False},
        {"name": "ollama",   "display": "Ollama (Local)",    "needs_key": False},
        {"name": "opencode", "display": "OpenCode API",      "needs_key": True},
        {"name": "openai",   "display": "OpenAI API",        "needs_key": True},
        {"name": "anthropic","display": "Anthropic",         "needs_key": True},
        {"name": "openrouter","display": "OpenRouter",       "needs_key": True},
    ]
