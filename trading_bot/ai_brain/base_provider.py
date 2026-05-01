from abc import ABC, abstractmethod


class BaseAIProvider(ABC):
    @abstractmethod
    async def ask(self, system_prompt: str, user_prompt: str) -> str:
        pass

    @property
    @abstractmethod
    def needs_api_key(self) -> bool:
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass