import asyncio
import json

import structlog
from groq import AsyncGroq, RateLimitError, APIStatusError

import google.generativeai as genai

from app.core.config import settings
from app.llm.base import LLMProvider, ReviewAnalysisResult

log = structlog.get_logger(__name__)

# Pricing per million tokens (USD) — update if Groq changes rates
_GROQ_PRICING: dict[str, dict[str, float]] = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "mixtral-8x7b-32768":      {"input": 0.24, "output": 0.24},
}
_DEFAULT_PRICING = {"input": 0.59, "output": 0.79}

_SYSTEM_PROMPT = (
    "You are an expert literary analyst helping independent authors understand "
    "their book reviews. Analyse the review carefully and return a JSON object "
    "matching the provided schema. Be precise and consistent."
)

_USER_PROMPT = """\
Analyse this book review and return structured JSON:

{review_text}
"""

# Retry policy for rate-limit errors
_MAX_RETRIES = 4
_BASE_BACKOFF = 1.0  # seconds


async def _with_backoff(coro_fn, *args, **kwargs):
    """
    Call an async coroutine function with exponential backoff on 429s.
    Raises the last exception if all retries are exhausted.
    """
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_fn(*args, **kwargs)
        except RateLimitError as exc:
            if attempt == _MAX_RETRIES - 1:
                log.error("llm.rate_limit.exhausted", attempts=_MAX_RETRIES)
                raise
            wait = _BASE_BACKOFF * (2 ** attempt)
            log.warning("llm.rate_limit", attempt=attempt + 1, wait_seconds=wait)
            await asyncio.sleep(wait)
        except APIStatusError as exc:
            # 5xx errors — retry once, then give up
            if exc.status_code >= 500 and attempt < 1:
                await asyncio.sleep(_BASE_BACKOFF)
                continue
            raise


class GroqProvider(LLMProvider):
    """
    LLM analysis via Groq (llama-3.3-70b-versatile by default).
    Embeddings via Google text-embedding-004 (Groq has no embedding API).
    """

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required for the Groq provider.")
        if not settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is required for embeddings even when using the "
                "Groq provider (Groq has no embedding model)."
            )
        self._client = AsyncGroq(api_key=settings.groq_api_key)
        self._model = settings.llm_model
        genai.configure(api_key=settings.gemini_api_key)

    # ── Analysis ───────────────────────────────────────────────────────────────

    async def analyze_review(self, review_text: str) -> ReviewAnalysisResult:
        schema = ReviewAnalysisResult.model_json_schema()

        async def _call():
            return await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _USER_PROMPT.format(review_text=review_text)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "ReviewAnalysis", "schema": schema, "strict": True},
                },
                temperature=0.1,  # low temp for consistent structured output
            )

        response = await _with_backoff(_call)
        raw = response.choices[0].message.content

        log.info(
            "llm.analyze.complete",
            model=self._model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )

        return ReviewAnalysisResult.model_validate(json.loads(raw))

    # ── Embeddings (via Google) ────────────────────────────────────────────────

    async def embed_text(self, text: str) -> list[float]:
        """
        Uses Google text-embedding-004 (768 dims) since Groq has no embedding API.
        Runs in a thread pool to avoid blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="RETRIEVAL_DOCUMENT",
            ),
        )
        return result["embedding"]

    # ── Cost estimation ────────────────────────────────────────────────────────

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = _GROQ_PRICING.get(self._model, _DEFAULT_PRICING)
        return (
            prompt_tokens * pricing["input"]
            + completion_tokens * pricing["output"]
        ) / 1_000_000
