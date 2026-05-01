import os
from typing import Optional

from anthropic import AsyncAnthropic

from ai_brain.base_provider import BaseAIProvider
from core.security import get_credential


class AnthropicProvider(BaseAIProvider):
    def __init__(
        self, api_key: Optional[str] = None, model: Optional[str] = None
    ):
        self.api_key = api_key or get_credential("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or get_credential("ANTHROPIC_MODEL")
        if self.model is None:
            self.model = "claude-3-5-sonnet-20241022"
        self._client: Optional[AsyncAnthropic] = None

    @property
    def needs_api_key(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def ask(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        try:
            response = await client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.7,
                max_tokens=500,
            )
            return response.content[0].text
        except Exception as e:
            import json
            return json.dumps({"action": "HOLD", "error": f"Anthropic API error: {str(e)}"})

    async def test_connection(self) -> dict:
        if not self.api_key:
            return {
                "connected": False,
                "error": "Anthropic API key not configured",
                "available_models": [m["name"] for m in self._fallback_models()],
            }
        try:
            await self._get_client().messages.create(
                model=self.model,
                messages=[{"role": "user", "content": "Reply OK"}],
                max_tokens=2,
            )
            models = await self.list_models()
            return {"connected": True, "model": self.model, "available_models": [m["name"] for m in models]}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    def _fallback_models(self) -> list[dict]:
        return [
            {"name": "claude-sonnet-4-5"},
            {"name": "claude-opus-4-1"},
            {"name": "claude-3-5-sonnet-20241022"},
            {"name": "claude-3-5-haiku-20241022"},
        ]

    async def list_models(self) -> list[dict]:
        if not self.api_key:
            return self._fallback_models()
        client = self._get_client()
        try:
            result = await client.models.list()
            data = getattr(result, "data", result)
            return [
                {"name": getattr(model, "id", None) or getattr(model, "name", None) or str(model)}
                for model in data
            ]
        except Exception:
            # Older anthropic SDKs may not expose a models endpoint.
            return self._fallback_models()


def is_configured() -> bool:
    return get_credential("ANTHROPIC_API_KEY") is not None or os.environ.get("ANTHROPIC_API_KEY") is not None
