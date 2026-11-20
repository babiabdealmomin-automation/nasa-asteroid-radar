"""
Interactive Telegram Bot Listener (Long-Polling).
Handles /start, /lang commands, and inline button callbacks so end-users
can choose their preferred language ('ar' or 'en') and toggle alerts in place.
"""

import logging
import os
import sys
import threading
import time
from typing import Any, Dict

import httpx
from dotenv import load_dotenv
from src.ai_analyzer import AsteroidAIAnalyzer
from src.database import RadarDatabase
from src.main import run_pipeline
from src.notifier import TelegramNotifier
from src.simulator import run_physics_simulation

logger = logging.getLogger("bot")


def configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


class InteractiveRadarBot:
    """Listens for Telegram user commands and inline language button clicks."""

    def __init__(self) -> None:
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required to run the interactive bot.")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.db = RadarDatabase()
        self.notifier = TelegramNotifier(bot_token=self.bot_token)
        self.ai_analyzer = AsteroidAIAnalyzer()

    def _api_call(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with httpx.Client(timeout=40.0) as client:
            resp = client.post(f"{self.base_url}/{method}", json=payload)
            if resp.status_code != 200:
                logger.error("Telegram API %s error (%s): %s", method, resp.status_code, resp.text)
            resp.raise_for_status()
            return resp.json()

    def _run_pipeline_async(self, chat_id: str) -> None:
        """Runs the NASA/Gemini pipeline in a background thread so the bot never freezes."""
        def _worker() -> None:
            try:
                run_pipeline(force_resend_chat_id=chat_id)
            except Exception as exc:
                logger.exception("Background pipeline failed for chat_id=%s: %s", chat_id, exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_message(self, message: Dict[str, Any]) -> None:
        chat_id = str(message["chat"]["id"])
        text = (message.get("text") or "").strip().lower()

        if text.startswith("/start") or text.startswith("/lang"):
            current_lang = self.db.get_user_language(chat_id)
            self.db.set_user_language(chat_id, current_lang)
            self.notifier.send_language_picker(chat_id=chat_id, current_lang=current_lang)
            logger.info("Sent language picker to chat_id=%s", chat_id)

        elif text.startswith("/scan") or text.startswith("/radar"):
            current_lang = self.db.get_user_language(chat_id)
            wait_msg = (
                "🛰️ <b>Scanning NASA NeoWs telemetry...</b> Please wait."
                if current_lang == "en"
                else "🛰️ <b>جاري فحص بيانات رادار NASA الآن...</b> يرجى الانتظار."
            )
            self._api_call("sendMessage", {"chat_id": chat_id, "text": wait_msg, "parse_mode": "HTML"})
            self._run_pipeline_async(chat_id)

    def _deliver_latest_to_user(self, chat_id: str, lang: str) -> None:
        """Immediately sends cached alerts in the chosen language in a non-blocking background thread."""
        def _worker() -> None:
            records = self.db.get_recent_cached_records(limit=5)
            if not records:
                fetching_msg = (
                    "📡 <b>Fetching live telemetry from NASA...</b>"
                    if lang == "en"
                    else "📡 <b>جاري سحب البيانات الحية من وكالة NASA لأول مرة...</b>"
                )
                self._api_call("sendMessage", {"chat_id": chat_id, "text": fetching_msg, "parse_mode": "HTML"})
                run_pipeline(force_resend_chat_id=chat_id)
                return

            if lang == "en" and any(not r.get("summary_en") for r in records):
                translating_msg = "⏳ <b>Generating English scientific briefs for all tracked asteroids...</b>"
                self._api_call("sendMessage", {"chat_id": chat_id, "text": translating_msg, "parse_mode": "HTML"})

            for r in records:
                if lang == "en" and not r.get("summary_en"):
                    try:
                        sim_check = run_physics_simulation(
                            diameter_max_m=r["diameter_max_m"],
                            velocity_kps=r["velocity_kps"],
                            miss_distance_km=r["miss_distance_km"],
                            is_hazardous=bool(r["is_hazardous"]),
                        )
                        assessment = self.ai_analyzer.analyze_asteroid(
                            record=r,
                            baseline_risk=sim_check.baseline_risk_tier,
                        )
                        self.db.save_ai_assessment(
                            neo_id=r["neo_id"],
                            risk_level=assessment.risk_level,
                            size_comparison_ar=assessment.size_comparison_ar,
                            summary_ar=assessment.summary_ar,
                            size_comparison_en=assessment.size_comparison_en,
                            summary_en=assessment.summary_en,
                        )
                        r = self.db.get_asteroid_by_id(r["neo_id"]) or r
                    except Exception as exc:
                        logger.warning("English backfill failed for neo_id=%s: %s", r["neo_id"], exc)

                self.notifier.send_asteroid_alert(r, chat_id=chat_id, lang=lang)

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_callback(self, callback: Dict[str, Any]) -> None:
        callback_id = callback["id"]
        data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = str(message["chat"]["id"])
        message_id = message["message_id"]

        if data.startswith("set_lang:"):
            chosen_lang = data.split(":", 1)[1]
            self.db.set_user_language(chat_id, chosen_lang)

            confirm_text = (
                "✅ <b>Language set to English!</b>\nSending latest NASA asteroid telemetry below..."
                if chosen_lang == "en"
                else "✅ <b>تم اختيار اللغة العربية!</b>\nجاري عرض أحدث تقارير رادار NASA أدناه..."
            )
            self._api_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": confirm_text,
                    "parse_mode": "HTML",
                },
            )
            self._api_call("answerCallbackQuery", {"callback_query_id": callback_id})
            logger.info("User chat_id=%s updated preference to lang=%s", chat_id, chosen_lang)
            self._deliver_latest_to_user(chat_id=chat_id, lang=chosen_lang)

        elif data == "open_lang_menu":
            current_lang = self.db.get_user_language(chat_id)
            self.notifier.send_language_picker(chat_id=chat_id, current_lang=current_lang)
            self._api_call("answerCallbackQuery", {"callback_query_id": callback_id})

        elif data == "scan_now":
            current_lang = self.db.get_user_language(chat_id)
            toast = "Scanning NASA feed..." if current_lang == "en" else "جاري فحص رادار ناسا..."
            self._api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": toast})
            self._run_pipeline_async(chat_id)

        elif data.startswith("toggle:"):
            _, neo_id, target_lang = data.split(":", 2)
            self.db.set_user_language(chat_id, target_lang)
            record = self.db.get_asteroid_by_id(neo_id)

            # Backfill bilingual AI text on-demand if this record was cached prior to EN support
            if record and target_lang == "en" and not record.get("summary_en"):
                self._api_call(
                    "answerCallbackQuery",
                    {"callback_query_id": callback_id, "text": "Generating English brief..."},
                )
                try:
                    sim_check = run_physics_simulation(
                        diameter_max_m=record["diameter_max_m"],
                        velocity_kps=record["velocity_kps"],
                        miss_distance_km=record["miss_distance_km"],
                        is_hazardous=bool(record["is_hazardous"]),
                    )
                    assessment = self.ai_analyzer.analyze_asteroid(
                        record=record,
                        baseline_risk=sim_check.baseline_risk_tier,
                    )
                    self.db.save_ai_assessment(
                        neo_id=neo_id,
                        risk_level=assessment.risk_level,
                        size_comparison_ar=assessment.size_comparison_ar,
                        summary_ar=assessment.summary_ar,
                        size_comparison_en=assessment.size_comparison_en,
                        summary_en=assessment.summary_en,
                    )
                    record = self.db.get_asteroid_by_id(neo_id)
                except Exception as exc:
                    logger.warning("On-demand English backfill failed for neo_id=%s: %s", neo_id, exc)

            if record:
                new_text = self.notifier.format_message(record, lang=target_lang)
                new_keyboard = self.notifier.build_alert_keyboard(neo_id=neo_id, current_lang=target_lang)
                self._api_call(
                    "editMessageText",
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": new_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "reply_markup": new_keyboard,
                    },
                )
            toast = "Switched to English" if target_lang == "en" else "تم التبديل إلى العربية"
            try:
                self._api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": toast})
            except Exception:
                pass

    def run_forever(self) -> None:
        logger.info("=== Clearing old webhooks & starting Telegram Bot ===")
        try:
            self._api_call("deleteWebhook", {"drop_pending_updates": False})
        except Exception as exc:
            logger.warning("Could not clear webhook: %s", exc)

        offset = 0
        while True:
            try:
                result = self._api_call(
                    "getUpdates",
                    {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                )
                for update in result.get("result", []):
                    offset = update["update_id"] + 1
                    if "message" in update:
                        self._handle_message(update["message"])
                    elif "callback_query" in update:
                        self._handle_callback(update["callback_query"])
            except Exception as exc:
                logger.warning("Polling cycle error (retrying in 3s): %s", exc)
                time.sleep(3.0)


if __name__ == "__main__":
    load_dotenv()
    configure_logging()
    bot = InteractiveRadarBot()
    bot.run_forever()