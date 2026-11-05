from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings, get_settings


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] | None = None


class LLMClient(Protocol):
    async def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> LLMResponse: ...


class OpenAILLM:
    def __init__(self, settings: Settings | None = None) -> None:
        from openai import AsyncOpenAI

        self.settings = settings or get_settings()
        if not self.settings.openai_api_key.strip():
            raise ValueError(
                "OPENAI_API_KEY is required. Copy .env.example to .env and set a key."
            )
        self.client = AsyncOpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_base_url,
        )
        self.model = self.settings.openai_model

    async def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> LLMResponse:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
        )
        content = (resp.choices[0].message.content or "").strip()
        usage = resp.usage
        return LLMResponse(
            content=content,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )


def build_llm(settings: Settings | None = None) -> LLMClient:
    return OpenAILLM(settings or get_settings())
