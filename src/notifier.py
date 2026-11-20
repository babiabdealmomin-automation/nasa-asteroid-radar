"""
Telegram Bot API Dispatcher with Interactive Language Selection.
Formats alerts in the user's chosen language ('ar' or 'en') and attaches
inline buttons allowing one-tap language switching directly inside Telegram.
"""

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Dispatches localized formatted alerts and interactive inline keyboards."""

    def __init__(
        self,
        bot_token: str | None = None,
        default_chat_id: str | None = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.default_chat_id = default_chat_id or os.getenv("TELEGRAM_CHAT_ID")

    def is_configured(self) -> bool:
        if not self.bot_token or "your_telegram" in self.bot_token:
            logger.warning("Telegram bot token missing or placeholder.")
            return False
        return True

    def send_asteroid_alert(
        self,
        record: Dict[str, Any],
        chat_id: Optional[str] = None,
        lang: str = "ar",
    ) -> bool:
        """Formats and transmits an individual asteroid report in the target user's language."""
        target_chat = str(chat_id or self.default_chat_id or "")
        if not self.is_configured() or not target_chat:
            return False

        message = self.format_message(record, lang=lang)
        keyboard = self.build_alert_keyboard(neo_id=record["neo_id"], current_lang=lang)
        return self._post_telegram_message(target_chat, message, reply_markup=keyboard)

    def send_language_picker(self, chat_id: str, current_lang: str = "ar") -> bool:
        """Sends an interactive language selection menu to the Telegram user."""
        if not self.is_configured():
            return False

        text = (
            "🛰️ <b>مرحباً بك في رادار NASA للأجرام القريبة من الأرض</b>\n"
            "Welcome to the <b>NASA Near-Earth Object Telemetry Radar</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "اختر لغتك المفضلة لاستلام التنبيهات والتحليلات العلمية:\n"
            "Please select your preferred language for alerts & scientific briefs:"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": f"{'✅ ' if current_lang == 'ar' else ''}🇸🇦 العربية",
                        "callback_data": "set_lang:ar",
                    },
                    {
                        "text": f"{'✅ ' if current_lang == 'en' else ''}🇬🇧 English",
                        "callback_data": "set_lang:en",
                    },
                ]
            ]
        }
        return self._post_telegram_message(str(chat_id), text, reply_markup=keyboard)

    def send_system_banner(
        self,
        status_title: str,
        details: str,
        chat_id: Optional[str] = None,
        lang: str = "ar",
    ) -> bool:
        """Dispatches a system health banner during upstream outages."""
        target_chat = str(chat_id or self.default_chat_id or "")
        if not self.is_configured() or not target_chat:
            return False

        prefix = "System Status Alert" if lang == "en" else "تنبيه حالة النظام"
        text = (
            f"⚠️ <b>{prefix} | {status_title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{details}"
        )
        return self._post_telegram_message(target_chat, text)

    def build_alert_keyboard(self, neo_id: str, current_lang: str) -> Dict[str, Any]:
        """Creates an inline button underneath each alert to toggle language on the fly."""
        if current_lang == "en":
            toggle_btn = {"text": "🇸🇦 التبديل إلى العربية", "callback_data": f"toggle:{neo_id}:ar"}
        else:
            toggle_btn = {"text": "🇬🇧 Switch to English", "callback_data": f"toggle:{neo_id}:en"}

        settings_btn = {"text": "⚙️ Language / اللغة", "callback_data": "open_lang_menu"}
        return {"inline_keyboard": [[toggle_btn], [settings_btn]]}

    def format_message(self, r: Dict[str, Any], lang: str = "ar") -> str:
        """Builds a localized HTML message supporting both AI-enriched and Fallback states."""
        ai_completed = r.get("ai_status") == "COMPLETED"
        risk = r.get("risk_level") or "Medium"

        if lang == "en":
            risk_badges = {
                "Low": "🟢 Low",
                "Medium": "🟡 Medium",
                "High": "🟠 High",
                "Critical": "🔴 Critical",
            }
            badge = risk_badges.get(risk, f"⚪ {risk}")

            header = (
                f"🛰️ <b>NASA Near-Earth Object Telemetry Radar</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🪨 <b>Object:</b> <code>{r['name']}</code>\n"
                f"⚠️ <b>Risk Tier:</b> {badge}\n"
                f"📅 <b>Close Approach:</b> {r['approach_date']} UTC\n\n"
                f"📊 <b>Telemetry & Deterministic Physics Simulation:</b>\n"
                f"• <b>Max Diameter:</b> {r['diameter_max_m']:,.1f} m\n"
                f"• <b>Relative Velocity:</b> {r['velocity_kps']:.2f} km/s\n"
                f"• <b>Miss Distance:</b> {r['miss_distance_km']:,.0f} km ({r['lunar_distance_ratio']}x LD)\n"
                f"• <b>Kinetic Energy (Ek):</b> {r['kinetic_energy_mt']:,.4f} Megatons TNT\n"
            )

            if ai_completed:
                comp = r.get("size_comparison_en") or r.get("size_comparison") or "N/A"
                summary = r.get("summary_en") or r.get("summary_ar") or "N/A"
                return header + (
                    f"\n📏 <b>Scale Comparison:</b> {comp}\n"
                    f"🧠 <b>Scientific Brief:</b>\n{summary}\n"
                )

            return header + (
                f"\n⚙️ <i>[Fallback Mode: Displaying raw NASA telemetry and deterministic physics metrics due to AI quota limits]</i>\n"
            )

        # Arabic ('ar')
        risk_badges = {
            "Low": "🟢 منخفضة (Low)",
            "Medium": "🟡 متوسطة (Medium)",
            "High": "🟠 مرتفعة (High)",
            "Critical": "🔴 حرجة (Critical)",
        }
        badge = risk_badges.get(risk, f"⚪ {risk}")

        header = (
            f"🛰️ <b>رادار NASA للأجرام القريبة من الأرض</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🪨 <b>الجرم:</b> <code>{r['name']}</code>\n"
            f"⚠️ <b>درجة الخطورة:</b> {badge}\n"
            f"📅 <b>ذروة الاقتراب:</b> {r['approach_date']} UTC\n\n"
            f"📊 <b>المعطيات الفلكية والمحاكاة الفيزيائية:</b>\n"
            f"• <b>القطر الأقصى:</b> {r['diameter_max_m']:,.1f} متر\n"
            f"• <b>السرعة النسبية:</b> {r['velocity_kps']:.2f} كم/ث\n"
            f"• <b>المسافة عن الأرض:</b> {r['miss_distance_km']:,.0f} كم ({r['lunar_distance_ratio']}x مسافة القمر)\n"
            f"• <b>الطاقة الحركية (Ek):</b> {r['kinetic_energy_mt']:,.4f} ميغاطن TNT\n"
        )

        if ai_completed:
            return header + (
                f"\n📏 <b>مقارنة الحجم:</b> {r['size_comparison']}\n"
                f"🧠 <b>التحليل العلمي:</b>\n{r['summary_ar']}\n"
            )

        return header + (
            f"\n⚙️ <i>[وضع الطوارئ: تم عرض البيانات الفلكية والمحاكاة الفيزيائية مباشرة بسبب بلوغ حد استهلاك الذكاء الاصطناعي]</i>\n"
        )

    def _post_telegram_message(
        self,
        chat_id: str,
        html_text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.error("Telegram dispatch failed for chat_id=%s: %s", chat_id, exc)
            return False