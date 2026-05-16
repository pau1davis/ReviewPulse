import asyncio
import json

import structlog
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable

from app.core.config import settings
from app.llm.base import LLMProvider, ReviewAnalysisResult

log = structlog.get_logger(__name__)

# Pricing per million tokens (USD)
_GEMINI_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.0-flash":  {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash":  {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro":    {"input": 1.25,  "output": 5.00},
}
_DEFAULT_PRICING = {"input": 0.075, "output": 0.30}

_SYSTEM_PROMPT = (
    "You are an expert literary analyst helping independent authors understand "
    "their book reviews. Analyse the review carefully and return a JSON object "
    "with exactly these fields:\n"
    "  sentiment: one of 'positive', 'mixed', 'negative'\n"
    "  sentiment_confidence: float between 0 and 1\n"
    "  themes: list of strings (pacing, characters, ending, cover, narration, "
    "plot, writing_style, world_building, price, length, humor, romance, mystery — "
    "add others if clearly present)\n"
    "  is_ai_generated: boolean\n"
    "  ai_generated_confidence: float between 0 and 1\n"
    "  summary: one sentence summarising the review from the author's perspective\n"
    "  is_actionable: boolean — true if the author could respond to or address this\n"
    "Return only the JSON object, no extra text."
)

_USER_PROMPT = """\
Analyse this book review and return structured JSON:

{review_text}
"""

_MAX_RETRIES = 4
_BASE_BACKOFF = 1.0


async def _with_backoff(coro_fn, *args, **kwargs):
    """Exponential backoff on ResourceExhausted (429) and 5xx errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_fn(*args, **kwargs)
        except ResourceExhausted as exc:
            if attempt == _MAX_RETRIES - 1:
                log.error("llm.rate_limit.exhausted", attempts=_MAX_RETRIES)
                raise
            wait = _BASE_BACKOFF * (2 ** attempt)
            log.warning("llm.rate_limit", attempt=attempt + 1, wait_seconds=wait)
            await asyncio.sleep(wait)
        except ServiceUnavailable:
            if attempt < 1:
                await asyncio.sleep(_BASE_BACKOFF)
                continue
            raise


class GeminiProvider(LLMProvider):
    """
    LLM analysis and embeddings via Google Gemini.
    Only requires GEMINI_API_KEY.
    """

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for the Gemini provider.")
        genai.configure(api_key=settings.gemini_api_key)
        self._model_name = settings.llm_model
        # Structured output: Gemini accepts a Pydantic model as response_schema
        self._model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=_SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

    # ── Analysis ───────────────────────────────────────────────────────────────

    async def analyze_review(self, review_text: str) -> ReviewAnalysisResult:
        prompt = _USER_PROMPT.format(review_text=review_text)

        async def _call():
            return await self._model.generate_content_async(prompt)

        response = await _with_backoff(_call)
        raw = response.text

        usage = response.usage_metadata
        log.info(
            "llm.analyze.complete",
            model=self._model_name,
            prompt_tokens=usage.prompt_token_count,
            completion_tokens=usage.candidates_token_count,
        )

        return ReviewAnalysisResult.model_validate(json.loads(raw))

    # ── Embeddings ────────────────────────────────────────────────────────────

    async def embed_text(self, text: str) -> list[float]:
        """Google text-embedding-004 — 768 dims, free tier."""
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
        pricing = _GEMINI_PRICING.get(self._model_name, _DEFAULT_PRICING)
        return (
            prompt_tokens * pricing["input"]
            + completion_tokens * pricing["output"]
        ) / 1_000_000
