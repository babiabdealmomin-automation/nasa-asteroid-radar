"""
Main Pipeline Orchestrator.
Coordinates ingestion, deterministic physics simulation, SQLite state persistence,
bilingual Gemini analysis, and personalized Telegram dispatching per user preference.
"""

import logging
import os
import sys
from dotenv import load_dotenv

from src.ai_analyzer import AsteroidAIAnalyzer, GeminiAnalyzerError
from src.database import RadarDatabase
from src.nasa_client import NasaApiUnavailableError, NasaNeoWsClient
from src.notifier import TelegramNotifier
from src.simulator import run_physics_simulation


def configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("system.log", encoding="utf-8"),
        ],
    )


def run_pipeline(force_resend_chat_id: str | None = None) -> None:
    """Executes one complete end-to-end telemetry and personalized alerting cycle."""
    load_dotenv()
    configure_logging()
    logger = logging.getLogger("main")

    default_lang = os.getenv("ALERT_LANG", "ar").strip().lower()
    if default_lang not in ("ar", "en"):
        default_lang = "ar"

    default_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    logger.info("=== Starting NASA Asteroid Radar Pipeline ===")

    db = RadarDatabase()
    nasa_client = NasaNeoWsClient()
    ai_analyzer = AsteroidAIAnalyzer()
    notifier = TelegramNotifier()

    subscribers = db.get_all_subscribed_users(
        fallback_chat_id=default_chat_id,
        default_lang=default_lang,
    )
    logger.info("Active Telegram subscribers: %d", len(subscribers))

    # Step 1: Ingest & Simulate Physics (with Outage Fallback)
    try:
        priority_asteroids = nasa_client.fetch_priority_asteroids()
        new_count = 0

        for item in priority_asteroids:
            sim_metrics = run_physics_simulation(
                diameter_max_m=item["diameter_max_m"],
                velocity_kps=item["velocity_kps"],
                miss_distance_km=item["miss_distance_km"],
                is_hazardous=bool(item["is_hazardous"]),
            )
            record = {
                **item,
                "kinetic_energy_mt": sim_metrics.kinetic_energy_mt,
                "lunar_distance_ratio": sim_metrics.lunar_distance_ratio,
            }
            if db.upsert_asteroid(record):
                new_count += 1

        logger.info("Ingestion complete. New priority records stored: %d", new_count)

    except NasaApiUnavailableError as exc:
        logger.warning("Switching to Offline Cached Mode due to NASA API failure: %s", exc)
        cached = db.get_recent_cached_records(limit=3)
        if cached:
            for user in subscribers:
                u_lang = user["lang"]
                if u_lang == "en":
                    summary_lines = "\n".join(
                        f"• <code>{c['name']}</code> | Dist: {c['lunar_distance_ratio']}x LD | Energy: {c['kinetic_energy_mt']} MT"
                        for c in cached
                    )
                    notifier.send_system_banner(
                        status_title="NASA API Unreachable",
                        details=f"Offline Cached Mode activated. Latest tracked objects:\n{summary_lines}",
                        chat_id=user["chat_id"],
                        lang="en",
                    )
                else:
                    summary_lines = "\n".join(
                        f"• <code>{c['name']}</code> | المسافة: {c['lunar_distance_ratio']}x LD | الطاقة: {c['kinetic_energy_mt']} MT"
                        for c in cached
                    )
                    notifier.send_system_banner(
                        status_title="تعذر الاتصال بخوادم NASA",
                        details=f"تم تفعيل وضع البيانات المخزنة مؤقتاً (Cached Mode). آخر الأجرام المرصودة:\n{summary_lines}",
                        chat_id=user["chat_id"],
                        lang="ar",
                    )
        return

    # Step 2: Process Pending Bilingual AI Assessments
    pending_records = db.get_pending_ai_assessments()
    logger.info("Records pending AI assessment: %d", len(pending_records))

    for row in pending_records:
        sim_check = run_physics_simulation(
            diameter_max_m=row["diameter_max_m"],
            velocity_kps=row["velocity_kps"],
            miss_distance_km=row["miss_distance_km"],
            is_hazardous=bool(row["is_hazardous"]),
        )
        try:
            assessment = ai_analyzer.analyze_asteroid(
                record=row,
                baseline_risk=sim_check.baseline_risk_tier,
            )
            db.save_ai_assessment(
                neo_id=row["neo_id"],
                risk_level=assessment.risk_level,
                size_comparison_ar=assessment.size_comparison_ar,
                summary_ar=assessment.summary_ar,
                size_comparison_en=assessment.size_comparison_en,
                summary_en=assessment.summary_en,
            )
            logger.info("Bilingual AI analysis completed for neo_id=%s", row["neo_id"])

        except GeminiAnalyzerError:
            logger.warning(
                "AI layer degraded for neo_id=%s. Marking FAILED to allow raw physics dispatch.",
                row["neo_id"],
            )
            db.mark_ai_failed(row["neo_id"])

    # Step 3: Dispatch Unnotified Alerts to Each Subscriber in Their Chosen Language
    unnotified = db.get_unnotified_records()
    logger.info("Unnotified alerts queued for dispatch: %d", len(unnotified))

    sent_count = 0
    for alert_record in unnotified:
        all_delivered = True
        for user in subscribers:
            delivered = notifier.send_asteroid_alert(
                alert_record,
                chat_id=user["chat_id"],
                lang=user["lang"],
            )
            if delivered:
                sent_count += 1
            else:
                all_delivered = False

        if all_delivered and subscribers:
            db.mark_as_notified(alert_record["neo_id"])

    # If triggered manually by a user (/scan or first-time setup) and no brand-new alerts existed
    if force_resend_chat_id and not unnotified:
        user_lang = db.get_user_language(force_resend_chat_id, default_lang=default_lang)
        recent = db.get_recent_cached_records(limit=5)
        for rec in recent:
            if notifier.send_asteroid_alert(rec, chat_id=force_resend_chat_id, lang=user_lang):
                sent_count += 1

    logger.info("=== Pipeline Finished | Dispatched Alerts: %d ===", sent_count)


if __name__ == "__main__":
    run_pipeline()