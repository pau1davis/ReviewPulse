"""
Unit tests for the LLM analysis layer.
No real API calls are made — the LLM provider is mocked throughout.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import ValidationError

from app.llm.base import LLMProvider, ReviewAnalysisResult, get_llm_provider


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_REVIEW = (
    "This book was absolutely fantastic! The characters felt real and the pacing "
    "kept me turning pages late into the night. The ending surprised me. Highly recommend."
)

VALID_ANALYSIS_PAYLOAD = {
    "sentiment": "positive",
    "sentiment_confidence": 0.91,
    "themes": ["characters", "pacing", "ending"],
    "is_ai_generated": False,
    "ai_generated_confidence": 0.04,
    "summary": "Reader loved the characters and pacing; found the ending surprising.",
    "is_actionable": False,
}

NEGATIVE_ACTIONABLE_PAYLOAD = {
    "sentiment": "negative",
    "sentiment_confidence": 0.85,
    "themes": ["pacing", "ending"],
    "is_ai_generated": False,
    "ai_generated_confidence": 0.10,
    "summary": "Reader felt the pacing dragged and the ending was unsatisfying.",
    "is_actionable": True,
}


class MockLLMProvider(LLMProvider):
    """
    In-memory LLM provider for tests.
    Returns configurable Pydantic-validated responses without any network calls.
    """

    def __init__(self, payload: dict | None = None):
        self._payload = payload or VALID_ANALYSIS_PAYLOAD

    async def analyze_review(self, review_text: str) -> ReviewAnalysisResult:
        return ReviewAnalysisResult.model_validate(self._payload)

    async def embed_text(self, text: str) -> list[float]:
        # Return a deterministic fake 768-dim vector
        return [0.1] * 768

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * 0.59 + completion_tokens * 0.79) / 1_000_000


# ── ReviewAnalysisResult schema tests ─────────────────────────────────────────

class TestReviewAnalysisResult:
    def test_valid_payload_parses(self):
        result = ReviewAnalysisResult.model_validate(VALID_ANALYSIS_PAYLOAD)
        assert result.sentiment == "positive"
        assert result.sentiment_confidence == pytest.approx(0.91)
        assert "characters" in result.themes
        assert result.is_ai_generated is False
        assert isinstance(result.summary, str) and len(result.summary) > 0

    def test_invalid_sentiment_raises(self):
        bad = {**VALID_ANALYSIS_PAYLOAD, "sentiment": "meh"}
        with pytest.raises(ValidationError):
            ReviewAnalysisResult.model_validate(bad)

    def test_confidence_out_of_range_raises(self):
        bad = {**VALID_ANALYSIS_PAYLOAD, "sentiment_confidence": 1.5}
        with pytest.raises(ValidationError):
            ReviewAnalysisResult.model_validate(bad)

    def test_negative_confidence_raises(self):
        bad = {**VALID_ANALYSIS_PAYLOAD, "ai_generated_confidence": -0.1}
        with pytest.raises(ValidationError):
            ReviewAnalysisResult.model_validate(bad)

    def test_missing_required_field_raises(self):
        bad = {k: v for k, v in VALID_ANALYSIS_PAYLOAD.items() if k != "summary"}
        with pytest.raises(ValidationError):
            ReviewAnalysisResult.model_validate(bad)

    def test_all_valid_sentiments(self):
        for sentiment in ("positive", "mixed", "negative"):
            payload = {**VALID_ANALYSIS_PAYLOAD, "sentiment": sentiment}
            result = ReviewAnalysisResult.model_validate(payload)
            assert result.sentiment == sentiment


# ── MockLLMProvider behaviour tests ───────────────────────────────────────────

class TestMockProvider:
    @pytest.mark.asyncio
    async def test_analyze_returns_valid_result(self):
        provider = MockLLMProvider()
        result = await provider.analyze_review(SAMPLE_REVIEW)
        assert isinstance(result, ReviewAnalysisResult)
        assert result.sentiment in ("positive", "mixed", "negative")

    @pytest.mark.asyncio
    async def test_analyze_negative_actionable(self):
        provider = MockLLMProvider(payload=NEGATIVE_ACTIONABLE_PAYLOAD)
        result = await provider.analyze_review(SAMPLE_REVIEW)
        assert result.sentiment == "negative"
        assert result.is_actionable is True

    @pytest.mark.asyncio
    async def test_embed_returns_768_dims(self):
        provider = MockLLMProvider()
        embedding = await provider.embed_text("some text")
        assert len(embedding) == 768
        assert all(isinstance(v, float) for v in embedding)

    def test_estimate_cost_is_positive(self):
        provider = MockLLMProvider()
        cost = provider.estimate_cost(prompt_tokens=500, completion_tokens=150)
        assert cost > 0.0

    def test_estimate_cost_scales_with_tokens(self):
        provider = MockLLMProvider()
        cheap = provider.estimate_cost(100, 50)
        expensive = provider.estimate_cost(1000, 500)
        assert expensive > cheap

    def test_estimate_cost_zero_tokens(self):
        provider = MockLLMProvider()
        cost = provider.estimate_cost(0, 0)
        assert cost == 0.0


# ── get_llm_provider factory tests ────────────────────────────────────────────

class TestProviderFactory:
    def test_unknown_provider_raises(self):
        with patch("app.llm.base.settings") as mock_settings:
            mock_settings.llm_provider = "cohere"
            with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
                get_llm_provider()

    def test_groq_provider_instantiated(self):
        with patch("app.llm.base.settings") as mock_settings:
            mock_settings.llm_provider = "groq"
            with patch("app.llm.groq_provider.GroqProvider") as MockGroq:
                MockGroq.return_value = MagicMock(spec=LLMProvider)
                provider = get_llm_provider()
                MockGroq.assert_called_once()

    def test_gemini_provider_instantiated(self):
        with patch("app.llm.base.settings") as mock_settings:
            mock_settings.llm_provider = "gemini"
            with patch("app.llm.gemini_provider.GeminiProvider") as MockGemini:
                MockGemini.return_value = MagicMock(spec=LLMProvider)
                provider = get_llm_provider()
                MockGemini.assert_called_once()
