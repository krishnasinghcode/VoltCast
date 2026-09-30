# mlMaster.md — ML / Intelligence Service (Part C)

**Read `CONTRACT.md` first.** You will NOT have access to data-gen, ingestion, or API code. This service only
touches Postgres (reads `agg_daily`, `simulation_vehicle_state`, `vehicles`, `batteries`; writes
`battery_predictions` and `alerts`). It never touches Kafka, Mongo, or Redis directly.

Deliverable: complete `services/ml/` folder, brought up by `docker compose up ml` as the `ml` container,
exposing a REST API on port 8003.

---

## 1. Purpose

Turns accumulated vehicle history (`agg_daily` rows) into the platform's one deep decision: predicted battery
degradation over the next 30 simulated days. Evaluates against a naive baseline. Converts prediction into a
risk level via product-policy thresholds (not a second ML model — simple rule on top of the prediction).

---

## 2. Inner architecture

```
services/ml/
├── Dockerfile
├── requirements.txt
├── src/
│   ├── main.py                # FastAPI app: /train, /predict, /predict/{vehicle_id}, /health
│   ├── config.py
│   ├── db.py                   # Postgres connection (SQLAlchemy)
│   ├── feature_engineering.py   # builds rolling 7d/30d feature vectors from agg_daily
│   ├── label_construction.py     # builds (features_at_day_X, label = SOH_X - SOH_X+30) training pairs
│   ├── baseline_model.py          # naive persistence baseline
│   ├── train.py                    # trains GradientBoostingRegressor, evaluates vs baseline, saves model
│   ├── predict.py                   # loads model, predicts for one or all vehicles, writes battery_predictions + alerts
│   └── risk_rules.py                 # predicted_degradation_30d_pct -> risk_level thresholds
├── models/                              # gitignored, model artifacts saved here (mounted volume)
├── tests/
│   ├── test_feature_engineering.py
│   ├── test_label_construction.py
│   └── test_risk_rules.py
└── README.md
```

requirements.txt (minimum):
```
fastapi
uvicorn[standard]
scikit-learn
pandas
numpy
psycopg2-binary
sqlalchemy
joblib
```

Dockerfile: `python:3.11-slim`, `CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8003"]`.
Mount `services/ml/models/` as a Docker volume so trained models survive container restarts.

---

## 3. Inputs

### 3.1 HTTP API (port 8003, all under `/api/v1/`)

| Method | Path | Body | Effect |
|---|---|---|---|
| POST | `/train` | `{"simulation_id": "uuid", "horizon_days": 30}` | runs the full train pipeline (§5–§6), saves model artifact + metrics, returns evaluation report |
| GET | `/train/status` | — | last training run's metrics (MAE, RMSE, baseline MAE, sample count, model_version, trained_at) |
| POST | `/predict` | `{"simulation_id": "uuid"}` (optional) | runs prediction for ALL vehicles with enough history, writes `battery_predictions` + `alerts` rows, returns summary counts by risk level |
| POST | `/predict/{vehicle_id}` | — | on-demand single-vehicle prediction, returns the prediction inline (also persisted) |
| GET | `/health` | — | `{"status":"ok","contract_version":"1.0.0","model_loaded": true/false}` |

### 3.2 Reads from Postgres (owned by other services, read-only here)

- `agg_daily` (from ingestion) — the feature source.
- `simulation_vehicle_state` (from data-gen) — the ground-truth SOH source for label construction during
  training only (never used at prediction time — prediction time only has features, not future ground truth,
  by construction).
- `vehicles`, `batteries`, `battery_chemistries` — for static context features (chemistry, nominal capacity,
  vehicle age).

---

## 4. Feature engineering (`feature_engineering.py`)

For a given `vehicle_id` and a given `as_of_date`, build one feature row from `agg_daily` rows in
`[as_of_date - 6, as_of_date]` (7-day window) and `[as_of_date - 29, as_of_date]` (30-day window):

```sql
-- 7-day window features (example — implement as a parametrized query, not literally this SQL string)
SELECT
  AVG(max_temp_c)                         AS avg_max_temp_7d,
  SUM(high_temp_hours)                    AS high_temp_hours_7d,
  SUM(fast_charge_sessions)               AS fast_charges_7d,
  SUM(deep_discharge_events)              AS deep_discharges_7d,
  SUM(distance_km)                        AS distance_7d,
  SUM(energy_consumed_kwh)                AS energy_consumed_7d,
  SUM(equivalent_full_cycles)             AS efc_7d,
  AVG(energy_consumed_kwh) / NULLIF(AVG(distance_km),0) AS energy_per_km_7d
FROM agg_daily
WHERE vehicle_id = :vehicle_id AND date BETWEEN :as_of_date - INTERVAL '6 days' AND :as_of_date
```

Repeat analogously for the 30-day window (cumulative exposures, trend = linear slope of `soh_pct` over the
window if available). Join in static context: `battery.nominal_capacity_kwh`, `chemistry.calendar_factor`,
`chemistry.cycle_factor`, vehicle age in days at `as_of_date`, current `soh_pct` (the most recent `agg_daily`
row ≤ `as_of_date`).

Final feature vector (columns, in this exact order — keep consistent between training and prediction):
```
current_soh, vehicle_age_days, nominal_capacity_kwh, chemistry_calendar_factor, chemistry_cycle_factor,
avg_max_temp_7d, high_temp_hours_7d, fast_charges_7d, deep_discharges_7d, distance_7d, energy_per_km_7d, efc_7d,
high_temp_hours_30d, fast_charges_30d, deep_discharges_30d, efc_30d, soh_trend_30d
```

If a vehicle doesn't have enough history for the full windows (e.g. fewer than 7 days of `agg_daily` rows),
skip it for training (not enough signal) and skip it for prediction (return `null`/omit from batch results
with a documented reason, don't crash).

---

## 5. Label construction for training (`label_construction.py`) — per CONTRACT.md §9 ground truth

Implements the SOT's training-example construction exactly:

```
For a given simulation_id with duration_days D:
  for as_of_day in range(7, D - horizon_days):     # need 7 days history behind, horizon_days ahead
      for each vehicle_id with data at as_of_day:
          features = feature_engineering.build(vehicle_id, as_of_day)
          soh_now   = simulation_vehicle_state.soh_pct at as_of_day  (or agg_daily.soh_pct)
          soh_future = simulation_vehicle_state.soh_pct at (as_of_day + horizon_days)
          label = soh_now - soh_future    # degradation_30d_pct, always >= 0 in this model
          emit (features, label)
```

To keep the training set manageable, sample `as_of_day` at a stride (e.g. every 3rd day) rather than every
single day — thousands of vehicles × tens of sampled days already gives a large enough dataset (tens of
thousands of rows) for a GBM to train well in seconds.

**Leakage check (do this and mention it explicitly in the Solution Document's AI/ML section):** features for
`as_of_day` must only use `agg_daily` rows with `date <= as_of_day`. The label uses a strictly later date.
Assert this in code (a simple `assert feature_window_end <= as_of_day`) — an actual leakage check, not just a
claim.

---

## 6. Models

### 6.1 Baseline (`baseline_model.py`)

Naive persistence: `predicted_degradation_30d = recent_daily_degradation_rate * 30`, where
`recent_daily_degradation_rate = (soh 7 days ago - soh_now) / 7` (i.e. "assume the recent trend continues
linearly"). No fitting required — this is a formula, not a trained model. Compute its MAE/RMSE against the
same held-out test set as the real model for a fair comparison.

### 6.2 Real model (`train.py`)

`sklearn.ensemble.GradientBoostingRegressor` (defaults are fine to start: `n_estimators=200, max_depth=3,
learning_rate=0.05`), trained on the feature vector from §4, target = label from §5.

Split: simple random 80/20 train/test split (stratifying by archetype if easy, not required). Report on the
held-out 20%: MAE, RMSE, and the same metrics for the baseline on the identical test rows, so the comparison
is apples-to-apples. Save the trained model with `joblib.dump()` to `services/ml/models/model_<version>.pkl`,
where `<version>` is a timestamp or incrementing integer. Update a `models/current.txt` pointer file with the
active version. `/train/status` and `/predict` read the current pointer.

Expected outcome to sanity-check against: the GBM model's MAE should be meaningfully lower than the baseline's
(e.g. 20-40% lower) BECAUSE the synthetic degradation formula (CONTRACT.md §9) is explicitly driven by
features the model has access to (thermal/fast-charge/deep-discharge exposure, archetype-correlated) that the
naive baseline ignores. If the model does NOT beat the baseline clearly, that's a signal the synthetic data
generation isn't injecting enough learnable signal — flag it back rather than silently shipping a weak result.

### 6.3 Risk derivation (`risk_rules.py`) — NOT ML, a product-policy rule on top of the prediction

```
if predicted_degradation_30d_pct >= 2.5:  risk_level = 'HIGH'
elif predicted_degradation_30d_pct >= 1.0: risk_level = 'MEDIUM'
else:                                       risk_level = 'LOW'
```

Document explicitly (per your own earlier decision) that these thresholds are project/demo policy, not an
industry standard — put this sentence directly in the Solution Document's ML section, it's an easy grading
point about honesty/rigor.

If `risk_level == 'HIGH'`, `/predict` also inserts an `alerts` row (`source='ML_PREDICTION'`,
`alert_type='HIGH_RISK_PREDICTION'`, `severity='WARNING'`, `prediction_id` set to the new prediction's id).

---

## 7. Outputs

1. `battery_predictions` rows (Postgres) — one per vehicle per prediction run, per CONTRACT.md §3 shape.
2. `alerts` rows for HIGH risk predictions.
3. Model artifact files under `services/ml/models/` (not committed to git if large; commit at least the final
   submission model plus its metrics JSON for reproducibility, per repository hygiene checklist).
4. `/train/status` JSON metrics report — this is what gets pasted into the Solution Document's ML evaluation
   section, so make sure it includes: `mae_model`, `mae_baseline`, `rmse_model`, `rmse_baseline`,
   `train_rows`, `test_rows`, `trained_at`, `model_version`.

---

## 8. What this service assumes about the rest of the system

- `agg_daily` has real rows for a completed or in-progress `LONGITUDINAL` (or `DEMO`) simulation before
  `/train` is called — calling `/train` on a simulation with fewer than ~14 days of aggregated history will
  produce a degenerate/empty training set; return a clear 400 error in that case rather than training garbage.
- `simulation_vehicle_state` retains historical snapshots for the full simulated duration (data-gen must not
  delete/overwrite old snapshots — it's an append-only per-day table keyed by `(simulation_id, vehicle_id)`
  ... **note:** CONTRACT.md §3 currently defines `simulation_vehicle_state` with PK
  `(simulation_id, vehicle_id)`, i.e. ONE row per vehicle per simulation, overwritten each tick — this is
  fine for data-gen's own "current state" use, but ML needs the historical SOH-per-day series. **Resolve
  this by reading historical SOH from `agg_daily.soh_pct` instead** (ingestion already snapshots it there
  daily, append-only by `(vehicle_id, date)` primary key) — do NOT expect `simulation_vehicle_state` to hold
  history. This is flagged here explicitly because it's exactly the kind of cross-module assumption mismatch
  this document exists to prevent.

---

## 9. Dependents

API service calls `/predict` (or reads `battery_predictions` directly from Postgres — either works, prefer
API reading Postgres directly for the dashboard's normal display, and calling this service's `/train` and
`/predict` endpoints only for the explicit "run prediction" / "retrain" buttons in the ML dashboard section).

---

## 10. How to run and verify standalone

Requires ingestion to have already populated `agg_daily` with at least ~2-3 simulated weeks of data for a
handful of vehicles (either from a real data-gen + ingestion run, or by hand-inserting synthetic `agg_daily`
rows via SQL for a few fake vehicles — write a small `tools/seed_fake_aggregates.py` script if data-gen/
ingestion aren't ready yet, so this service is independently testable).

```bash
docker compose up postgres ml
curl -X POST localhost:8003/api/v1/train -d '{"simulation_id":"<id>","horizon_days":30}'
curl localhost:8003/api/v1/train/status
curl -X POST localhost:8003/api/v1/predict -d '{"simulation_id":"<id>"}'
```

Confirm `battery_predictions` rows appear in Postgres and `mae_model < mae_baseline` in the status report.

---

## 11. Tests to include

- `test_feature_engineering.py`: given hand-crafted `agg_daily` rows, assert the computed 7d/30d aggregates
  match hand-calculated expected values.
- `test_label_construction.py`: assert the leakage check trips (raises) if fed a feature window that extends
  past `as_of_day`; assert label = soh_now - soh_future arithmetic is correct on a synthetic example.
- `test_risk_rules.py`: assert threshold boundaries produce the expected risk_level (test exactly at 1.0 and
  2.5 boundaries, not just comfortably inside each band).
