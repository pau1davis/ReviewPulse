from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings


# ── Structured output schemas ──────────────────────────────────────────────────
# Every LLM provider must return responses that validate against these models.
# We pass schemas to each provider's native structured-output API so no
# string parsing is needed — if the model returns garbage, Pydantic raises.

class ReviewAnalysisResult(BaseModel):
    sentiment: Literal["positive", "mixed", "negative"] = Field(
        description="Overall sentiment of the review."
    )
    sentiment_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the sentiment classification, 0–1."
    )
    themes: list[str] = Field(
        description=(
            "Themes mentioned in the review. Choose from: pacing, characters, "
            "ending, cover, narration, plot, writing_style, world_building, "
            "price, length, humor, romance, mystery. Add others if clearly present."
        )
    )
    is_ai_generated: bool = Field(
        description="Whether the review is likely written by an AI."
    )
    ai_generated_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence that the review is AI-generated, 0–1."
    )
    summary: str = Field(
        description="One sentence summarising the review from the author's perspective."
    )
    is_actionable: bool = Field(
        description=(
            "True if the review raises something the author could respond to "
            "or address in their next book."
        )
    )


class DraftReplyResult(BaseModel):
    reply: str = Field(
        description="The drafted reply, 80-120 words, professional and warm."
    )
    tone: Literal["professional", "warm", "empathetic"] = Field(
        description="The dominant tone of the reply."
    )


# ── Abstract provider interface ────────────────────────────────────────────────

class LLMProvider(ABC):
    """
    All LLM providers must implement this interface.
    The rest of the codebase only imports this class — never a concrete provider.
    """

    @abstractmethod
    async def analyze_review(self, review_text: str) -> ReviewAnalysisResult:
        """
        Run structured analysis on a single review body.
        Must return a validated ReviewAnalysisResult.
        Implementations are responsible for retries and rate-limit handling.
        """
        ...

    @abstractmethod
    async def draft_reply(self, review_text: str, book_title: str) -> DraftReplyResult:
        """
        Generate a draft public reply to an actionable review, written in first
        person as the author. Returned text is ~80-120 words, ready to post on
        Amazon/Goodreads after light editing.
        """
        ...

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """
        Return a 768-dimensional embedding vector for the given text.
        Used for pgvector semantic search.
        """
        ...

    @abstractmethod
    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Return estimated cost in USD for a given token usage."""
        ...


# ── Provider factory ───────────────────────────────────────────────────────────

def get_llm_provider() -> LLMProvider:
    """
    Returns the configured LLM provider based on settings.llm_provider.
    Import this instead of importing a concrete provider directly.
    """
    provider = settings.llm_provider.lower()

    if provider == "groq":
        from app.llm.groq_provider import GroqProvider
        return GroqProvider()
    elif provider == "gemini":
        from app.llm.gemini_provider import GeminiProvider
        return GeminiProvider()
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{provider}'. Valid options: groq, gemini"
        )
