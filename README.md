# NASA NeoWs Telemetry & Impact Simulation Pipeline

An automated ETL, deterministic physics simulation, and alerting service built on top of the NASA Near Earth Object Web Service (`NeoWs`). 

The system pulls daily asteroid telemetry, filters high-priority objects based on strict orbital thresholds, computes kinetic energy and lunar distance ratios locally, and generates structured bilingual risk briefs via Gemini API with schema validation. Designed with offline caching and graceful degradation so upstream API failures never break the pipeline.

---

## System Architecture

```text
+-------------------+       HTTPS / JSON       +-----------------------+
|   NASA NeoWs API  | -----------------------> |  Ingestion & Filter   |
+-------------------+      (Timeout: 10s)      |   (nasa_client.py)    |
                                               +-----------+-----------+
                                                           |
                                            Passes Thresholds (Hazardous /
                                            Dia >= 100m / Dist <= 5M km)
                                                           |
                                                           v
+-------------------+     SQLite Upsert        +-----------------------+
|   SQLite Engine   | <----------------------- | Deterministic Physics |
|   (database.py)   |                          |    (simulator.py)     |
+---------+---------+                          +-----------+-----------+
          |                                                |
          | Checks 'ai_status' & 'notified'                v
          |                                    +-----------------------+
          |   Fallback (Raw + Physics Only)    |  Gemini Structured AI |
          +----------------------------------> |   (ai_analyzer.py)    |
          |      If Quota / HTTP 429           +-----------+-----------+
          |                                                |
          +------------------------------------------------+
                                  |
                                  v
                      +-----------------------+
                      |   Telegram Dispatcher |
                      |     (notifier.py)     |
                      +-----------------------+
```

---

## Core Design Principles

1. **Zero-Hallucination Math (Hybrid Engine):** LLMs do not perform arithmetic in this pipeline. Mass, kinetic energy ($E_k = \frac{1}{2}mv^2$ in Megatons of TNT), and Lunar Distance ($LD$) ratios are computed deterministically in Python (`simulator.py`) before being passed to the LLM strictly for natural-language contextualization.
2. **Idempotency & State Tracking:** Every object is indexed by its `neo_id` primary key. Repeated cron executions within the same 24-hour window will not duplicate records, re-trigger LLM calls, or spam the notification channel.
3. **Graceful Degradation (Circuit Breaker Pattern):**
   - **NASA API Down / Timeout:** Pipeline aborts ingestion, logs `HTTP/ConnectError`, and serves the last known valid state from local SQLite storage.
   - **Gemini Quota Exceeded (`HTTP 429`):** Pipeline bypasses the AI layer, flags `ai_status = 'PENDING'` in SQLite, and dispatches the alert containing raw NASA telemetry and deterministic physics simulation results. Pending AI summaries are backfilled on subsequent runs.
   - **Telegram Dispatch Failure:** Record remains flagged as `notified = 0` and retries automatically on the next scheduled cycle without re-querying NASA or Gemini.

---

## Database Schema (SQLite 3)

Normalized 1:1 relational structure separating raw telemetry/physics from generative assessments:

```sql
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
    ai_status TEXT DEFAULT 'PENDING' CHECK(ai_status IN ('PENDING', 'COMPLETED', 'FAILED')),
    notified INTEGER DEFAULT 0
);
```

---

## Filtering Rules

Raw `NeoWs` feeds contain high volumes of non-critical rocks. To preserve compute and API quotas, objects are dropped at the ingestion edge unless they satisfy at least one condition:

- `is_potentially_hazardous_asteroid == True`
- `estimated_diameter_max >= 100.0` (meters)
- `miss_distance <= 5,000,000.0` (kilometers)

---

## Project Layout

```text
nasa-asteroid-radar/
├── .github/
│   └── workflows/
│       └── radar_cron.yml   # Automated 6-hour scheduled execution
├── src/
│   ├── database.py          # SQLite connection pool, migrations, state queries
│   ├── nasa_client.py       # HTTPX client with tenacity backoff & filtering
│   ├── simulator.py         # Kinetic energy (MT) & Lunar Distance math
│   ├── ai_analyzer.py       # Pydantic schema enforcement & Gemini SDK wrapper
│   ├── notifier.py          # Telegram Bot API dispatcher & fallback formatter
│   └── main.py              # Pipeline orchestrator & CLI entrypoint
├── tests/
│   └── test_simulator.py    # Unit tests for physics calculations & filters
├── .env.example             # Environment variable template
├── .gitignore
├── requirements.txt         # Pinned production dependencies
└── README.md
```

---

## Local Setup & Execution

### Prerequisites
- Python `3.12+`
- NASA Open API Key (`api.nasa.gov` - defaults to `DEMO_KEY` for local testing)
- Google Gemini API Key (`aistudio.google.com`)
- Telegram Bot Token & Target Chat ID

### Installation

```bash
git clone https://github.com/<your-username>/nasa-asteroid-radar.git
cd nasa-asteroid-radar

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env` and populate your credentials:

```ini
NASA_API_KEY=DEMO_KEY
GEMINI_API_KEY=your_gemini_key_here
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
DB_PATH=data/radar.db
LOG_LEVEL=INFO
```

### Run Pipeline

```bash
python -m src.main
```

---

## CI/CD & Scheduled Automation (GitHub Actions)

The pipeline runs headlessly every 6 hours via GitHub Actions (`.github/workflows/radar_cron.yml`). The SQLite database file is cached across workflow runs using `actions/cache` to preserve state and prevent duplicate alerts without requiring an external cloud database.

---
---

# نسخة التوثيق بالعربية (نظرة معمارية)

## نظرة عامة
نظام أتمتة وهندسة بيانات (ETL Pipeline) يقوم بسحب بيانات الأجرام القريبة من الأرض عبر واجهة وكالة ناسا (`NeoWs API`)، تصفيتها محلياً، وحساب مؤشرات الفيزياء الحركية (الطاقة الحركية بالميغاطن ونسبة المسافة القمرية) رياضياً عبر Python، ثم تمرير المخرجات لنموذج الذكاء الاصطناعي ضمن قالب `Pydantic` مقيد لإصدار تقارير رصد علمية وإرسالها عبر تيليجرام.

## قرارات هندسية رئيسية (Architecture Decisions)

* **فصل الحسابات الرياضية عن النموذج اللغوي:** لمنع أي خطأ حسابي أو هلوسة رقمية، يتولى ملف `simulator.py` كافة المعادلات الفيزيائية بالاعتماد على القطر والسرعة، بينما يقتصر دور النموذج اللغوي على صياغة السياق العلمي ومقارنة الأحجام فقط.
* **استمرارية الخدمة عند الأعطال (Fault Tolerance):**
  * **انقطاع خادم ناسا أو الشبكة:** يلتقط النظام استثناء الاتصال بعد مهلة 10 ثوانٍ، يسجل الخطأ في `system.log`، ويعتمد على آخر بيانات محفوظة في `SQLite` دون تعطل.
  * **بلوغ الحد الأقصى لطلبات Gemini (`Quota Exceeded`):** ينتقل النظام تلقائياً إلى وضع الطوارئ (Fallback Mode)، حيث يخزن البيانات الخام مع نتائج المحاكاة الفيزيائية ويرسل التنبيه للمستخدم بالأرقام الدقيقة دون انتظار الذكاء الاصطناعي، مع وضع علامة `PENDING` لمعالجتها في الدورة اللاحقة.
  * **منع التكرار (Idempotency):** استخدام `neo_id` كمفتاح أساسي مع تتبع حالة الإرسال `notified` يضمن عدم إرسال نفس التنبيه مرتين مهما تكرر تشغيل السكريبت.
