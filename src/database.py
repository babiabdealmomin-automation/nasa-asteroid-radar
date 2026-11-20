"""
Database persistence layer using SQLite 3.
Implements idempotent upserts, bilingual storage (AR/EN), user language preferences,
and state tracking for graceful degradation.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RadarDatabase:
    """Manages SQLite connections, schema migrations, user preferences, and pipeline state."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.getenv("DB_PATH", "data/radar.db")
        self._ensure_directory()
        self.init_schema()

    def _ensure_directory(self) -> None:
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_schema(self) -> None:
        """Creates normalized tables and migrates legacy columns if needed."""
        schema_sql = """
        CREATE TABLE IF NOT EXISTS asteroids_raw (
            neo_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            approach_date TEXT NOT NULL,
            diameter_max_m REAL NOT NULL,
            velocity_kps REAL NOT NULL,
            miss_distance_km REAL NOT NULL,
            is_hazardous INTEGER NOT NULL,
            kinetic_energy_mt REAL NOT NULL,
            lunar_distance_ratio REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ai_assessments (
            assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            neo_id TEXT UNIQUE NOT NULL REFERENCES asteroids_raw(neo_id) ON DELETE CASCADE,
            risk_level TEXT CHECK(risk_level IN ('Low', 'Medium', 'High', 'Critical')),
            size_comparison TEXT,
            summary_ar TEXT,
            size_comparison_en TEXT,
            summary_en TEXT,
            ai_status TEXT DEFAULT 'PENDING' CHECK(ai_status IN ('PENDING', 'COMPLETED', 'FAILED')),
            notified INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_preferences (
            chat_id TEXT PRIMARY KEY,
            lang TEXT DEFAULT 'ar' CHECK(lang IN ('ar', 'en')),
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
        try:
            with self._get_connection() as conn:
                conn.executescript(schema_sql)
                # Safe migration for existing databases created prior to bilingual columns
                existing_cols = {
                    row["name"] for row in conn.execute("PRAGMA table_info(ai_assessments);").fetchall()
                }
                if "size_comparison_en" not in existing_cols:
                    conn.execute("ALTER TABLE ai_assessments ADD COLUMN size_comparison_en TEXT;")
                if "summary_en" not in existing_cols:
                    conn.execute("ALTER TABLE ai_assessments ADD COLUMN summary_en TEXT;")
            logger.debug("Database schema initialized successfully at %s", self.db_path)
        except sqlite3.Error as exc:
            logger.critical("Failed to initialize database schema: %s", exc)
            raise

    # --- User Language Preferences ---

    def set_user_language(self, chat_id: str, lang: str) -> None:
        """Saves or updates a Telegram user's chosen language ('ar' or 'en')."""
        clean_lang = lang if lang in ("ar", "en") else "ar"
        sql = """
        INSERT INTO user_preferences (chat_id, lang, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET
            lang = excluded.lang,
            updated_at = CURRENT_TIMESTAMP;
        """
        with self._get_connection() as conn:
            conn.execute(sql, (str(chat_id), clean_lang))

    def get_user_language(self, chat_id: str, default_lang: str = "ar") -> str:
        """Retrieves the user's selected language, falling back to default_lang."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT lang FROM user_preferences WHERE chat_id = ?;",
                (str(chat_id),),
            ).fetchone()
            return row["lang"] if row else default_lang

    def get_all_subscribed_users(self, fallback_chat_id: Optional[str] = None, default_lang: str = "ar") -> List[Dict[str, str]]:
        """Returns all registered Telegram subscribers and their preferred language."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT chat_id, lang FROM user_preferences;").fetchall()
            users = [{"chat_id": row["chat_id"], "lang": row["lang"]} for row in rows]

        if not users and fallback_chat_id:
            self.set_user_language(fallback_chat_id, default_lang)
            return [{"chat_id": str(fallback_chat_id), "lang": default_lang}]
        return users

    # --- Asteroid Telemetry & AI Assessments ---

    def upsert_asteroid(self, record: Dict[str, Any]) -> bool:
        insert_raw_sql = """
        INSERT OR IGNORE INTO asteroids_raw (
            neo_id, name, approach_date, diameter_max_m, velocity_kps,
            miss_distance_km, is_hazardous, kinetic_energy_mt, lunar_distance_ratio
        ) VALUES (
            :neo_id, :name, :approach_date, :diameter_max_m, :velocity_kps,
            :miss_distance_km, :is_hazardous, :kinetic_energy_mt, :lunar_distance_ratio
        );
        """
        insert_ai_stub_sql = """
        INSERT OR IGNORE INTO ai_assessments (neo_id, ai_status, notified)
        VALUES (:neo_id, 'PENDING', 0);
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(insert_raw_sql, record)
                is_new = cursor.rowcount > 0
                conn.execute(insert_ai_stub_sql, {"neo_id": record["neo_id"]})
                return is_new
        except sqlite3.Error as exc:
            logger.error("Database error upserting neo_id=%s: %s", record.get("neo_id"), exc)
            return False

    def get_pending_ai_assessments(self) -> List[Dict[str, Any]]:
        query = """
        SELECT r.*, a.ai_status, a.notified
        FROM asteroids_raw r
        JOIN ai_assessments a ON r.neo_id = a.neo_id
        WHERE a.ai_status IN ('PENDING', 'FAILED')
           OR a.summary_en IS NULL
           OR a.size_comparison_en IS NULL
        ORDER BY r.approach_date ASC;
        """
        with self._get_connection() as conn:
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def save_ai_assessment(
        self,
        neo_id: str,
        risk_level: str,
        size_comparison_ar: str,
        summary_ar: str,
        size_comparison_en: str,
        summary_en: str,
    ) -> None:
        update_sql = """
        UPDATE ai_assessments
        SET risk_level = ?,
            size_comparison = ?,
            summary_ar = ?,
            size_comparison_en = ?,
            summary_en = ?,
            ai_status = 'COMPLETED',
            notified = 0
        WHERE neo_id = ?;
        """
        with self._get_connection() as conn:
            conn.execute(
                update_sql,
                (risk_level, size_comparison_ar, summary_ar, size_comparison_en, summary_en, neo_id),
            )

    def mark_ai_failed(self, neo_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE ai_assessments SET ai_status = 'FAILED' WHERE neo_id = ?;",
                (neo_id,),
            )

    def get_asteroid_by_id(self, neo_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a single asteroid record (used for instant language toggle buttons)."""
        query = """
        SELECT r.*, a.risk_level, a.size_comparison, a.summary_ar,
               a.size_comparison_en, a.summary_en, a.ai_status
        FROM asteroids_raw r
        JOIN ai_assessments a ON r.neo_id = a.neo_id
        WHERE r.neo_id = ?;
        """
        with self._get_connection() as conn:
            row = conn.execute(query, (neo_id,)).fetchone()
            return dict(row) if row else None

    def get_unnotified_records(self) -> List[Dict[str, Any]]:
        query = """
        SELECT r.*, a.risk_level, a.size_comparison, a.summary_ar,
               a.size_comparison_en, a.summary_en, a.ai_status
        FROM asteroids_raw r
        JOIN ai_assessments a ON r.neo_id = a.neo_id
        WHERE a.notified = 0
        ORDER BY r.approach_date ASC;
        """
        with self._get_connection() as conn:
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def mark_as_notified(self, neo_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE ai_assessments SET notified = 1 WHERE neo_id = ?;",
                (neo_id,),
            )

    def get_recent_cached_records(self, limit: int = 5) -> List[Dict[str, Any]]:
        query = """
        SELECT r.*, a.risk_level, a.size_comparison, a.summary_ar,
               a.size_comparison_en, a.summary_en, a.ai_status
        FROM asteroids_raw r
        JOIN ai_assessments a ON r.neo_id = a.neo_id
        ORDER BY r.approach_date DESC
        LIMIT ?;
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, (limit,)).fetchall()
            return [dict(row) for row in rows]