import os
from typing import Optional

from openai import AsyncOpenAI

from ai_brain.base_provider import BaseAIProvider
from core.security import get_credential


class OpenAIProvider(BaseAIProvider):
    def __init__(
        self, api_key: Optional[str] = None, model: Optional[str] = None
    ):
        self.api_key = (
            api_key
            or get_credential("OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        self.model = (
            model
            or get_credential("OPENAI_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o"
        )
        if self.model is None:
            self.model = "gpt-4o"
        self.organization = (
            get_credential("OPENAI_ORG_ID")
            or os.environ.get("OPENAI_ORGANIZATION")
        )
        self.project = (
            get_credential("OPENAI_PROJECT_ID")
            or os.environ.get("OPENAI_PROJECT")
        )
        self._client: Optional[AsyncOpenAI] = None

    @property
    def needs_api_key(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                organization=self.organization,
                project=self.project,
            )
        return self._client

    async def ask(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=500,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            import json
            return json.dumps({"action": "HOLD", "error": f"OpenAI API error: {str(e)}"})

    async def test_connection(self) -> dict:
        client = self._get_client()
        try:
            models = await client.models.list()
            model_ids = [m.id for m in models.data]
            return {
                "connected": True,
                "available_models": model_ids,
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}


def is_configured() -> bool:
    return (
        get_credential("OPENAI_API_KEY") is not None
        or os.environ.get("OPENAI_API_KEY") is not None
    )
