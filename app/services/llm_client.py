import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, LLMError)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> tuple[T, dict[str, Any]]:
        if not self.settings.deepseek_api_key:
            raise LLMError("DEEPSEEK_API_KEY is not configured")

        payload = {
            "model": model or self.settings.default_deepseek_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(base_url=self.settings.deepseek_base_url, timeout=60) as client:
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            return schema.model_validate(parsed), {"raw": data, "usage": data.get("usage", {})}
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMError(f"DeepSeek returned malformed JSON for {schema.__name__}: {exc}") from exc

