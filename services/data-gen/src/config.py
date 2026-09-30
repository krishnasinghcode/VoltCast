"""Environment-derived defaults. A request body always overrides these for a single run;
this module's values are never mutated by a request."""
import os

from dotenv import load_dotenv

load_dotenv()

CONTRACT_VERSION = os.getenv("CONTRACT_VERSION", "1.0.0")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "telemetry.raw")
KAFKA_PARTITIONS = int(os.getenv("KAFKA_PARTITIONS", "12"))

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "cvi")
POSTGRES_USER = os.getenv("POSTGRES_USER", "cvi_app")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme_local_only")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)

DEFAULT_SEED = int(os.getenv("DEFAULT_SEED", "42"))
DEFAULT_VEHICLE_COUNT = int(os.getenv("DEFAULT_VEHICLE_COUNT", "100000"))
DEFAULT_ACTIVE_VEHICLE_COUNT = int(os.getenv("DEFAULT_ACTIVE_VEHICLE_COUNT", "1500"))

# Degradation model constants (CONTRACT.md §9) — copied verbatim, same names.
K_CAL = float(os.getenv("K_CAL", "0.0008"))
K_CYC = float(os.getenv("K_CYC", "0.015"))
K_THERM = float(os.getenv("K_THERM", "0.004"))
K_FAST = float(os.getenv("K_FAST", "0.01"))
K_DEEP = float(os.getenv("K_DEEP", "0.008"))
DAILY_DEGRADATION_MAX = 0.05
SOH_FLOOR = 60.0
THERMAL_THRESHOLD_C = 35.0
FAST_CHARGE_POWER_FRACTION = 0.6
DEEP_DISCHARGE_SOC_THRESHOLD = 20.0
