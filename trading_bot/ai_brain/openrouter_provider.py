import os
import requests
from typing import Optional

from ai_brain.base_provider import BaseAIProvider
from core.security import get_credential


class OpenRouterProvider(BaseAIProvider):
    def __init__(
        self, api_key: Optional[str] = None, model: Optional[str] = None
    ):
        self.api_key = api_key or get_credential("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        self.model = model or get_credential("OPENROUTER_MODEL")
        if self.model is None:
            self.model = "openai/gpt-4o"
        self.base_url = "https://openrouter.ai/api/v1"

    @property
    def needs_api_key(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "openrouter"

    async def ask(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            import json
            return json.dumps({"action": "HOLD", "error": f"OpenRouter API error: {str(e)}"})

    async def test_connection(self) -> dict:
        if not self.api_key:
            return {"connected": False, "error": "OpenRouter API key not configured"}
        chat_url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            response = requests.post(
                chat_url,
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                    "max_tokens": 2,
                },
                timeout=20,
            )
            response.raise_for_status()
            models = await self.list_models()
            return {"connected": True, "available_models": [m["name"] for m in models]}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    async def list_models(self) -> list[dict]:
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            models = response.json().get("data", [])
            return [{"name": m["id"]} for m in models if isinstance(m, dict) and m.get("id")]
        except Exception as e:
            return [{"name": "", "error": str(e)}]


def is_configured() -> bool:
    return get_credential("OPENROUTER_API_KEY") is not None or os.environ.get("OPENROUTER_API_KEY") is not None
