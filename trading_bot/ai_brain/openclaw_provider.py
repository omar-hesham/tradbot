import json
import httpx
from typing import Optional

from ai_brain.base_provider import BaseAIProvider
from core.security import get_credential

DEFAULT_BASE_URL = "http://72.60.88.88:46160"


class OpenclawProvider(BaseAIProvider):
    """
    Openclaw / Oxylabs AI Gateway provider.
    Uses httpx directly against the OpenAI-compatible surface:
      POST /v1/chat/completions
      GET  /v1/models
    The OpenAI-compatible surface must be enabled in the gateway config first.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.base_url = (
            (base_url or get_credential("OPENCLAW_BASE_URL") or DEFAULT_BASE_URL)
            .rstrip("/")
        )
        self.api_key = api_key or get_credential("OPENCLAW_API_KEY") or ""
        self.model   = model   or get_credential("OPENCLAW_MODEL")   or ""

    @property
    def needs_api_key(self) -> bool:
        return False

    @property
    def provider_name(self) -> str:
        return "openclaw"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _html_error(self, url: str) -> str:
        return (
            f"Gateway at {url} returned an HTML page instead of JSON. "
            "The OpenAI-compatible API surface is not enabled. "
            "Enable it in your OpenClaw gateway config and check the correct port."
        )

    async def ask(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        payload: dict = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        }
        if self.model:
            payload["model"] = self.model

        try:
            async with httpx.AsyncClient(timeout=60, verify=False) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                body = resp.text

                if body.strip().startswith("<"):
                    return json.dumps({"action": "HOLD", "error": self._html_error(url)})

                resp.raise_for_status()
                data = resp.json()

            if "choices" in data:
                return data["choices"][0].get("message", {}).get("content", "") or ""
            if "content" in data:
                return data["content"]
            return json.dumps(data)

        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            return json.dumps({"action": "HOLD", "error": f"Openclaw HTTP {e.response.status_code}: {body}"})
        except Exception as e:
            return json.dumps({"action": "HOLD", "error": f"Openclaw error: {str(e)}"})

    async def list_models(self) -> list[dict]:
        url = f"{self.base_url}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                resp = await client.get(url, headers=self._headers())
                body = resp.text
                if body.strip().startswith("<"):
                    return [{"name": "", "error": self._html_error(url)}]
                resp.raise_for_status()
                data = resp.json()
            models = data.get("data", data.get("models", []))
            return [{"name": m.get("id") or m.get("name") or str(m)} for m in models]
        except Exception as e:
            return [{"name": self.model or "", "error": str(e)}]

    async def test_connection(self) -> dict:
        """Probe /v1/models and return a clear error if the API surface is off."""
        url = f"{self.base_url}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                resp = await client.get(url, headers=self._headers())
                body = resp.text

                if body.strip().startswith("<"):
                    return {"connected": False, "error": self._html_error(url)}

                resp.raise_for_status()
                data = resp.json()
                models = data.get("data", data.get("models", []))
                names = [m.get("id") or m.get("name") or str(m) for m in models]
                return {"connected": True, "available_models": names}

        except httpx.HTTPStatusError as e:
            return {"connected": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"connected": False, "error": str(e)}


def is_configured() -> bool:
    return bool(get_credential("OPENCLAW_BASE_URL") or DEFAULT_BASE_URL)
