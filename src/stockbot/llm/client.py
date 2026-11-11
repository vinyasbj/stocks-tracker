import logging
import re
from typing import TypeVar

import ollama
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
SENTENCE_END = re.compile(r"(.*[.!?][\"')]?)(?:\s|$)", re.DOTALL)  # "24.3%" doesn't end a sentence: no space follows


def drop_partial_sentence(text: str) -> str:
    """Text cut off by the token cap ends mid-sentence: keep up to the last complete one, if there is one."""
    m = SENTENCE_END.match(text)
    return m.group(1) if m else text


class LLM:
    """Local model via Ollama. num_ctx is set on every call: Ollama's default silently truncates.

    `timeout` bounds each call: the bot handles one update at a time, so a call that never returns
    would block every command.
    """

    def __init__(self, url: str, model: str, num_ctx: int, timeout: float | None = None):
        self.client = ollama.AsyncClient(host=url, timeout=timeout)
        self.model = model
        self.num_ctx = num_ctx

    async def chat(self, messages: list[dict], temperature: float = 0.3, max_tokens: int | None = None) -> str:
        """`max_tokens` caps the reply (Ollama's num_predict); a reply cut off by it loses its unfinished sentence."""
        options = {"temperature": temperature, "num_ctx": self.num_ctx}
        if max_tokens:
            options["num_predict"] = max_tokens
        resp = await self.client.chat(model=self.model, messages=messages, options=options)
        text = resp.message.content.strip()
        if max_tokens and getattr(resp, "done_reason", None) == "length":
            text = drop_partial_sentence(text)
        return text

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
