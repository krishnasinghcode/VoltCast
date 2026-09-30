import os

CONTRACT_VERSION = os.getenv("CONTRACT_VERSION", "1.0.0")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "telemetry.raw")
KAFKA_CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "ingestion-group")

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "cvi")
POSTGRES_USER = os.getenv("POSTGRES_USER", "cvi_app")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme_local_only")

POSTGRES_DSN = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

MONGO_HOST = os.getenv("MONGO_HOST", "mongo")
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_DB = os.getenv("MONGO_DB", "cvi_telemetry")
MONGO_URI = os.getenv("MONGO_URI", f"mongodb://{MONGO_HOST}:{MONGO_PORT}")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

RAW_RETENTION_DAYS = int(os.getenv("RAW_RETENTION_DAYS", "14"))

# Windowed aggregation tuning
FLUSH_INTERVAL_SECONDS = float(os.getenv("FLUSH_INTERVAL_SECONDS", "30"))
# LONGITUDINAL can delay one event by up to five 5-minute emission slots.
# Keep the watermark grace above that 25-minute maximum to avoid partial late-window upserts.
GRACE_PERIOD_SECONDS = float(os.getenv("GRACE_PERIOD_SECONDS", "1800"))  # 30 min

# Alert thresholds (CONTRACT.md / ingestionMaster.md §7)
HIGH_TEMP_THRESHOLD_C = float(os.getenv("HIGH_TEMP_THRESHOLD_C", "45"))
DEEP_DISCHARGE_THRESHOLD_PCT = float(os.getenv("DEEP_DISCHARGE_THRESHOLD_PCT", "15"))
ALERT_DEBOUNCE_SECONDS = float(os.getenv("ALERT_DEBOUNCE_SECONDS", "600"))  # 10 sim minutes
INGESTION_QUALITY_WINDOW_SECONDS = float(os.getenv("INGESTION_QUALITY_WINDOW_SECONDS", "60"))
INGESTION_QUALITY_REJECT_RATE = float(os.getenv("INGESTION_QUALITY_REJECT_RATE", "0.05"))

# Charging session classification: fast charge if max_power_seen > this fraction of battery rated max.
# No per-vehicle rated max is available to ingestion (owned by data-gen's `batteries` table), so a fixed
# reference ceiling is used as the practical proxy, documented as a deviation in README.
FAST_CHARGE_POWER_KW_THRESHOLD = float(os.getenv("FAST_CHARGE_POWER_KW_THRESHOLD", "0.6")) * float(
    os.getenv("BATTERY_MAX_CHARGE_POWER_KW", "150")
)

# Bloom filter dedup sizing
BLOOM_CAPACITY = int(os.getenv("BLOOM_CAPACITY", "500000"))
BLOOM_ERROR_RATE = float(os.getenv("BLOOM_ERROR_RATE", "0.001"))
