# ML / Intelligence Service — Battery Degradation Prediction

Part C of the Connected Vehicle Intelligence Platform (see `CONTRACT.md` and `mlMaster.md`).
Turns accumulated `agg_daily` history into a 30-day battery degradation forecast, evaluated
against a naive baseline, with a risk level derived by product-policy thresholds.

Touches Postgres only (`agg_daily`, `simulation_vehicle_state`, `vehicles`, `batteries`,
`battery_chemistries`; writes `battery_predictions` and `alerts`). Never touches Kafka, Mongo,
or Redis directly.

## Layout

```
Dockerfile
requirements.txt
src/
├── main.py                # FastAPI app: /train, /predict, /predict/{vehicle_id}, /health
├── config.py
├── db.py                   # Postgres connection (SQLAlchemy)
├── feature_engineering.py   # builds rolling 7d/30d feature vectors from agg_daily
├── label_construction.py     # builds (features_at_day_X, label = SOH_X - SOH_X+30) pairs
├── baseline_model.py          # naive persistence baseline
├── train.py                    # trains GradientBoostingRegressor, evaluates, saves model
├── predict.py                   # loads model, predicts, writes battery_predictions + alerts
└── risk_rules.py                 # predicted_degradation_30d_pct -> risk_level thresholds
models/                              # gitignored, model artifacts (mounted volume)
tests/
tools/seed_fake_aggregates.py       # seeds fake agg_daily rows for standalone testing
```

## Run

```bash
docker compose up postgres ml
```

Or standalone against a local Postgres with the CONTRACT.md schema already applied:

```bash
pip install -r requirements.txt
python tools/seed_fake_aggregates.py   # only if data-gen/ingestion haven't populated agg_daily yet
uvicorn src.main:app --host 0.0.0.0 --port 8003
```

## Verify

```bash
curl -X POST localhost:8003/api/v1/train -d '{"simulation_id":"<id>","horizon_days":30}'
curl localhost:8003/api/v1/train/status
curl -X POST localhost:8003/api/v1/predict -d '{"simulation_id":"<id>"}'
```

Confirm `battery_predictions` rows appear in Postgres and `mae_model < mae_baseline` in the
status report (`beats_baseline: true`).

## Design notes / declared decisions

- **Leakage guard**: `label_construction.build_training_example` asserts
  `feature_window_end <= as_of_date` on every training example — an actual runtime check, not
  just a claim.
- **`simulation_vehicle_state` history assumption (mlMaster.md §8)**: `simulation_vehicle_state`
  has PK `(simulation_id, vehicle_id)` — one row per vehicle, overwritten each tick — so it does
  NOT hold history. This service reads historical SOH exclusively from `agg_daily.soh_pct`
  (append-only by `(vehicle_id, date)`), per the contract's own documented resolution.
  `simulation_vehicle_state` is never queried by this service.
- **Risk thresholds** (`risk_rules.py`) are project/demo policy (>=2.5% -> HIGH, >=1.0% -> MEDIUM,
  else LOW), not an industry standard.
- **Missing history**: vehicles with fewer than 7 days of `agg_daily` rows are skipped (not
  errored) at both training and prediction time, per mlMaster.md §4.
- **ASSUMPTION/DEVIATION — `simulation_id` not enforced as a filter**: `agg_daily` and
  `vehicles` (CONTRACT.md §3) carry no `simulation_id` column, so `/train` and `/predict` accept
  `simulation_id` (per mlMaster.md §3.1) but cannot scope queries to it — `/train` uses it only
  to look up `duration_days` from `simulations`; both endpoints otherwise operate over all
  vehicles/history present in Postgres. Correct for this platform's single-simulation-at-a-time
  demo; would need `agg_daily.simulation_id` for multi-simulation isolation.
- **`/train` on under-aggregated data**: returns `400 INSUFFICIENT_HISTORY` rather than training
  on a degenerate/empty set (mlMaster.md §8).

## Tests

```bash
pytest tests/ -v
```

- `test_feature_engineering.py` — 7d/30d window aggregates against hand-calculated values.
- `test_label_construction.py` — leakage-check assertion trips correctly; label arithmetic.
- `test_risk_rules.py` — threshold boundaries at exactly 1.0 and 2.5.
