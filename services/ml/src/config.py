import os

CONTRACT_VERSION = "1.0.0"

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "cvi")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "cvi_app")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "changeme_local_only")

DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

MODEL_DIR = os.environ.get("MODEL_DIR", "models")
HORIZON_DAYS_DEFAULT = int(os.environ.get("HORIZON_DAYS_DEFAULT", "30"))
FEATURE_STRIDE_DAYS = int(os.environ.get("FEATURE_STRIDE_DAYS", "3"))
MIN_HISTORY_DAYS = int(os.environ.get("MIN_HISTORY_DAYS", "14"))
FEATURE_WINDOW_SHORT_DAYS = 7
FEATURE_WINDOW_LONG_DAYS = 30

# Originally 2.5 / 1.0 as placeholder guesses before any real data existed. Recalibrated against
# actual predicted_degradation_30d_pct values from a real 200-vehicle/90-day run once available:
# HEALTHY avg 0.14, NORMAL avg 0.21, STRESSED avg 0.41 (p90 0.67), BAD avg 0.46 (p90 0.91) --
# the old thresholds meant NOTHING could ever cross HIGH (BAD's max was 0.94 < 2.5). These values
# create a real, meaningful LOW/MEDIUM/HIGH spread across that observed range instead.
RISK_THRESHOLD_HIGH = 0.45
RISK_THRESHOLD_MEDIUM = 0.20

FEATURE_COLUMNS = [
    "current_soh",
    "vehicle_age_days",
    "nominal_capacity_kwh",
    "chemistry_calendar_factor",
    "chemistry_cycle_factor",
    "avg_max_temp_7d",
    "high_temp_hours_7d",
    "fast_charges_7d",
    "deep_discharges_7d",
    "distance_7d",
    "energy_per_km_7d",
    "efc_7d",
    "high_temp_hours_30d",
    "fast_charges_30d",
    "deep_discharges_30d",
    "efc_30d",
    "soh_trend_30d",
]
