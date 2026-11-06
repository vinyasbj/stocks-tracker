import logging
from typing import TypeVar

import ollama
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLM:
    """Local model via Ollama. num_ctx is set on every call: Ollama's default silently truncates."""

    def __init__(self, url: str, model: str, num_ctx: int):
        self.client = ollama.AsyncClient(host=url)
        self.model = model
        self.num_ctx = num_ctx

    async def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        resp = await self.client.chat(
            model=self.model,
            messages=messages,
            options={"temperature": temperature, "num_ctx": self.num_ctx},
        )
        return resp.message.content.strip()

    async def structured(self, messages: list[dict], schema: type[T]) -> T:
        """JSON constrained to `schema`. One retry, because small models occasionally drop a field."""
        last: Exception | None = None
        for _ in range(2):
            resp = await self.client.chat(
                model=self.model,
                messages=messages,
                format=schema.model_json_schema(),
                options={"temperature": 0, "num_ctx": self.num_ctx},
            )
            try:
                return schema.model_validate_json(resp.message.content)
            except ValidationError as e:
                last = e
                log.warning("LLM returned invalid %s: %s", schema.__name__, resp.message.content[:300])
        raise last  # type: ignore[misc]
