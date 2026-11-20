"""
Structured Bilingual AI Contextualizer using Google Gemini SDK and Pydantic.
Generates both Arabic and English scientific briefs in a single inference call
so end-users can switch languages dynamically in Telegram without re-querying Gemini.
"""

import logging
import os
import time
from typing import Any, Dict, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AsteroidAssessmentSchema(BaseModel):
    """Strict bilingual output schema enforced on Gemini responses."""

    risk_level: Literal["Low", "Medium", "High", "Critical"] = Field(
        description="Must match the deterministic baseline risk tier provided in the input."
    )
    size_comparison_ar: str = Field(
        description="Realistic physical size comparison in Arabic (e.g., 'يعادل ارتفاع مبنى من 30 طابقاً')."
    )
    summary_ar: str = Field(
        description="Serious, objective scientific summary in Arabic (max 3 sentences) without sensationalism."
    )
    size_comparison_en: str = Field(
        description="Realistic physical size comparison in English (e.g., 'Comparable to a 30-story building')."
    )
    summary_en: str = Field(
        description="Serious, objective scientific summary in English (max 3 sentences) without sensationalism."
    )


class GeminiAnalyzerError(Exception):
    """Raised when all Gemini models fail after retries."""


class AsteroidAIAnalyzer:
    """Wraps Gemini API with structured JSON outputs, retry backoff, and model fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        fallback_model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.primary_model = model_name or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.fallback_model = fallback_model or os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")

        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            logger.warning("GEMINI_API_KEY is missing or placeholder. AI layer will trigger fallback.")
            self.client = None
        else:
            self.client = genai.Client(api_key=self.api_key)

    def _build_prompts(self, record: Dict[str, Any], baseline_risk: str) -> tuple[str, str]:
        system_instruction = (
            "You are a specialized aerospace telemetry analyst. Your task is to generate a serious, "
            "objective scientific brief in BOTH Arabic and English based strictly on the provided figures.\n"
            "Strict Rules:\n"
            "1. NEVER alter, round differently, or invent any number (diameter, velocity, distance, kinetic energy).\n"
            "2. The 'risk_level' field MUST match the deterministic baseline risk tier provided in the input.\n"
            "3. Avoid media sensationalism; maintain rigorous scientific clarity in both languages."
        )
        prompt = (
            f"Verified NASA & Physics Simulation Telemetry:\n"
            f"- Object Name: {record['name']} (ID: {record['neo_id']})\n"
            f"- Close Approach Time: {record['approach_date']} UTC\n"
            f"- Max Estimated Diameter: {record['diameter_max_m']} meters\n"
            f"- Relative Velocity: {record['velocity_kps']} km/s\n"
            f"- Miss Distance: {record['miss_distance_km']:,.2f} km ({record['lunar_distance_ratio']}x Lunar Distance)\n"
            f"- Estimated Kinetic Energy: {record['kinetic_energy_mt']} Megatons TNT\n"
            f"- Deterministic Physics Risk Tier: {baseline_risk}\n"
            f"- NASA Potentially Hazardous Flag: {'Yes' if record['is_hazardous'] else 'No'}"
        )
        return system_instruction, prompt

    def _invoke_model(self, model: str, system_instruction: str, prompt: str) -> AsteroidAssessmentSchema:
        assert self.client is not None
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AsteroidAssessmentSchema,
                temperature=0.2,
            ),
        )
        if not response.text:
            raise RuntimeError(f"Empty response from model {model}")
        return AsteroidAssessmentSchema.model_validate_json(response.text)

    def analyze_asteroid(self, record: Dict[str, Any], baseline_risk: str) -> AsteroidAssessmentSchema:
        """Generates structured bilingual analysis with retry backoff and fallback model."""
        if self.client is None:
            raise GeminiAnalyzerError("Gemini client not configured.")

        system_instruction, prompt = self._build_prompts(record, baseline_risk)
        candidate_models = [self.primary_model]
        if self.fallback_model and self.fallback_model != self.primary_model:
            candidate_models.append(self.fallback_model)

        last_exc: Exception | None = None

        for model in candidate_models:
            for attempt in range(1, 3):
                try:
                    validated = self._invoke_model(model, system_instruction, prompt)
                    validated.risk_level = baseline_risk  # type: ignore[assignment]
                    return validated
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Model %s attempt %d failed for neo_id=%s: %s",
                        model,
                        attempt,
                        record["neo_id"],
                        exc,
                    )
                    if attempt < 2:
                        time.sleep(2.0)

        logger.error("All Gemini models exhausted for neo_id=%s: %s", record["neo_id"], last_exc)
        raise GeminiAnalyzerError(f"AI inference failed across all candidate models: {last_exc}") from last_exc