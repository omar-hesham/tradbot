import json
import httpx
import os
import asyncio
from typing import Optional, List, Dict, Any

from ai_brain.base_provider import BaseAIProvider
from core.security import get_credential


class OllamaProvider(BaseAIProvider):
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url or get_credential("OLLAMA_BASE_URL") or "http://localhost:11434"
        self.model = model or get_credential("OLLAMA_MODEL")
        if not self.model:
            self.model = "qwen2.5:7b"
        
        self.api_key = api_key or get_credential("OLLAMA_API_KEY") or os.environ.get("OLLAMA_API_KEY")
        self._is_cloud = ":cloud" in self.model or self._check_if_cloud_model()

    def _check_if_cloud_model(self) -> bool:
        cloud_suffixes = [":cloud", "-cloud", "-cloud:latest"]
        return any(self.model.endswith(s) for s in cloud_suffixes)

    @property
    def needs_api_key(self) -> bool:
        # Ollama only requires an API key in cloud mode; a locally-configured
        # key is optional and doesn't mean the provider "needs" one.
        return self._is_cloud

    @property
    def provider_name(self) -> str:
        return "ollama"

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def list_models(self) -> List[Dict[str, Any]]:
        print(f"[Ollama] Fetching models from {self.base_url}...")
        try:
            url = f"{self.base_url}/api/tags"
            async with httpx.AsyncClient(headers=self._get_headers()) as client:
                print(f"[Ollama] Sending GET request to {url}")
                response = await client.get(url, timeout=5.0)
                print(f"[Ollama] Response status: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                models = data.get("models", [])
                print(f"[Ollama] Found {len(models)} models")
                return [
                    {
                        "name": m.get("name", ""),
                        "size": m.get("size", 0),
                        "modified_at": m.get("modified_at", ""),
                    }
                    for m in models
                ]
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            print(f"[Ollama] Connection failed: {str(e)}")
            return [{"name": f"Error: Ollama not running on {self.base_url}", "error": True}]
        except httpx.HTTPError as e:
            print(f"[Ollama] HTTP Error: {str(e)}")
            return [{"name": f"Error: {str(e)}", "error": True}]
        except Exception as e:
            print(f"[Ollama] Unexpected error: {str(e)}")
            return []

    async def test_connection(self) -> Dict[str, Any]:
        print(f"[Ollama] Testing connection to {self.base_url}...")
        try:
            url = f"{self.base_url}/api/tags"
            async with httpx.AsyncClient(headers=self._get_headers()) as client:
                response = await client.get(url, timeout=5.0)
                response.raise_for_status()
                data = response.json()
                models = data.get("models", [])
                print(f"[Ollama] Test success, models: {len(models)}")
                return {
                    "connected": True,
                    "available_models": [m["name"] for m in models],
                    "cloud_mode": self._is_cloud,
                }
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            print(f"[Ollama] Test failed (connection): {str(e)}")
            return {"connected": False, "error": f"Ollama not running on {self.base_url}"}
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            print(f"[Ollama] Test failed (http/timeout): {str(e)}")
            return {"connected": False, "error": str(e)}

    async def web_search(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Ollama API key required for web search"}
        
        try:
            url = "https://ollama.com/api/web_search"
            headers = self._get_headers()
            payload = {"query": query, "max_results": max_results}
            
            async with httpx.AsyncClient(headers=headers) as client:
                response = await client.post(url, json=payload, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                
                results = data.get("results", [])
                return {
                    "results": [
                        {
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "content": r.get("content", "")[:500],
                        }
                        for r in results
                    ]
                }
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            return {"error": str(e)}

    async def ask(self, system_prompt: str, user_prompt: str) -> str:
        if self._is_cloud and not self.api_key:
            return '{"action": "HOLD", "error": "Ollama API key required for cloud models"}'

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": f"System: {system_prompt}\n\nUser: {user_prompt}",
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 500,
            }
        }

        try:
            async with httpx.AsyncClient(headers=self._get_headers()) as client:
                response = await client.post(url, json=payload, timeout=180.0)
                response.raise_for_status()
                data = response.json()
                content = data.get("response")
                if content is None:
                    return json.dumps(
                        {
                            "action": "HOLD",
                            "error": "Ollama returned an empty response body",
                        }
                    )
                return str(content).strip()
        except asyncio.TimeoutError:
            return json.dumps({"action": "HOLD", "error": "Ollama request timeout"})
        except httpx.HTTPError as e:
            return json.dumps({"action": "HOLD", "error": f"Ollama connection failed: {str(e)}"})

    async def ask_with_context(
        self,
        system_prompt: str,
        user_prompt: str,
        search_results: Optional[List[Dict]] = None
    ) -> str:
        if search_results:
            search_context = "\n\nRecent search results:\n"
            for i, r in enumerate(search_results[:3], 1):
                search_context += f"{i}. {r.get('title', '')}: {r.get('content', '')[:200]}...\n"
            user_prompt = user_prompt + search_context

        return await self.ask(system_prompt, user_prompt)


def is_configured() -> bool:
    return True


async def get_ollama_models() -> List[Dict[str, Any]]:
    provider = OllamaProvider()
    return await provider.list_models()


async def test_ollama_web_search(api_key: str, query: str = "test") -> Dict[str, Any]:
    provider = OllamaProvider(api_key=api_key)
    return await provider.web_search(query)
