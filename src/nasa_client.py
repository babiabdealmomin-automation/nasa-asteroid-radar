"""
NASA NeoWs API Ingestion & Filtering Client.
Fetches daily Near-Earth Object telemetry, applies orbital priority filters,
and handles transient HTTP failures with exponential backoff.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

NASA_NEOWS_FEED_URL = "https://api.nasa.gov/neo/rest/v1/feed"

# Filtering Business Rules (Edge Filtering to reduce noise & API costs)
MIN_DIAMETER_THRESHOLD_M = 100.0
MAX_DISTANCE_THRESHOLD_KM = 5_000_000.0


class NasaApiUnavailableError(Exception):
    """Raised when NASA NeoWs API fails after retries or returns an error status."""


class NasaNeoWsClient:
    """HTTP client for querying and filtering NASA Near-Earth Object telemetry."""

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.api_key = api_key or os.getenv("NASA_API_KEY", "DEMO_KEY")
        self.timeout = timeout_seconds

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _fetch_raw_feed(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Executes the HTTP GET request to NASA NeoWs with retry protection."""
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "api_key": self.api_key,
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(NASA_NEOWS_FEED_URL, params=params)
            response.raise_for_status()
            return response.json()

    def fetch_priority_asteroids(self) -> List[Dict[str, Any]]:
        """
        Retrieves today's NEOs, flattens the nested JSON structure,
        and filters for objects meeting critical size, distance, or hazard thresholds.
        """
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info("Querying NASA NeoWs feed for UTC date: %s", today_utc)

        try:
            payload = self._fetch_raw_feed(start_date=today_utc, end_date=today_utc)
        except Exception as exc:
            logger.error("NASA NeoWs ingestion failed: %s", exc)
            raise NasaApiUnavailableError(f"Upstream NASA API error: {exc}") from exc

        near_earth_objects = payload.get("near_earth_objects", {})
        daily_list = near_earth_objects.get(today_utc, [])
        logger.info("Total raw objects received from NASA: %d", len(daily_list))

        filtered_asteroids: List[Dict[str, Any]] = []

        for raw_obj in daily_list:
            parsed = self._parse_and_filter(raw_obj)
            if parsed is not None:
                filtered_asteroids.append(parsed)

        logger.info(
            "Filtered priority asteroids passing thresholds: %d / %d",
            len(filtered_asteroids),
            len(daily_list),
        )
        return filtered_asteroids

    def _parse_and_filter(self, raw_obj: Dict[str, Any]) -> Dict[str, Any] | None:
        """Extracts required fields and drops non-critical objects."""
        try:
            neo_id = str(raw_obj["id"])
            name = str(raw_obj["name"]).strip("()")
            is_hazardous = bool(raw_obj.get("is_potentially_hazardous_asteroid", False))

            diameter_meters = raw_obj["estimated_diameter"]["meters"]
            diameter_max_m = float(diameter_meters["estimated_diameter_max"])

            close_approaches = raw_obj.get("close_approach_data", [])
            if not close_approaches:
                return None

            approach = close_approaches[0]
            approach_date = str(approach.get("close_approach_date_full") or approach.get("close_approach_date"))
            velocity_kps = float(approach["relative_velocity"]["kilometers_per_second"])
            miss_distance_km = float(approach["miss_distance"]["kilometers"])

            # Apply Business Filtering Rules
            passes_filter = (
                is_hazardous
                or diameter_max_m >= MIN_DIAMETER_THRESHOLD_M
                or miss_distance_km <= MAX_DISTANCE_THRESHOLD_KM
            )

            if not passes_filter:
                return None

            return {
                "neo_id": neo_id,
                "name": name,
                "approach_date": approach_date,
                "diameter_max_m": round(diameter_max_m, 2),
                "velocity_kps": round(velocity_kps, 3),
                "miss_distance_km": round(miss_distance_km, 2),
                "is_hazardous": 1 if is_hazardous else 0,
            }
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping malformed NASA object payload: %s", exc)
            return None