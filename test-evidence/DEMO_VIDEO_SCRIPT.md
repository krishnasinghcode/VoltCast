# Demo Video Script (5:00 max)

Record in 1080p, clear audio. Follow the timing loosely — a few seconds over/under per segment is fine,
total must stay under 5:00 (it gets cut at 5:00 if longer).

## Before you hit record — prep checklist

- [ ] Docker stack up and healthy: `docker compose ps` — all 9 containers `Up`.
- [ ] Dashboard open at `http://localhost:3000`, logged out (so login is visible on camera).
- [ ] A second browser tab/terminal ready to show architecture diagram (`docs/diagrams/architecture.png`)
      and the k6/chaos result files, so you're not fumbling for files mid-recording.
- [ ] Know the real numbers by heart (or have them on a sticky note just off-camera): **MAE 0.0165 vs
      baseline 0.0326 (49% better)**, **p95 60.84ms / p99 93.4ms**, **chaos recovery 5.9s, zero data loss**,
      **risk split 112 LOW / 69 MEDIUM / 19 HIGH**.
- [ ] Decide which vehicle you'll drill into for the live demo — pick a BAD-archetype one with a HIGH
      prediction so the risk feature visibly means something on screen (query if needed:
      `SELECT vehicle_id FROM battery_predictions WHERE risk_level='HIGH' LIMIT 1;`).
- [ ] If you're doing the live "trigger a simulation" bit, have the create/start curl commands ready in a
      terminal, pre-typed but not yet submitted.

---

## 0:00–0:30 — Problem

**Show:** your face / a title slide (optional), or just start talking over a blank dashboard login screen.

**Say (paraphrase in your own words, don't read verbatim):**
"Fleet managers running electric vehicles today can see a battery's current charge and current health — but
nothing about where it's headed. That's a real gap: with no forward signal, maintenance is reactive, not
proactive. To show how real this gap is, we actually tested it — when we first set risk thresholds for our
own prediction feature without any real data to calibrate against, every single vehicle came back LOW risk,
regardless of how badly it was behaving. That's the exact blind spot we're solving."

---

## 0:30–1:00 — Solution (one-line pitch)

**Show:** architecture diagram (`docs/diagrams/architecture.png`) full-screen for a few seconds.

**Say:**
"We built a connected-vehicle platform that ingests simulated fleet telemetry at scale, and predicts each
vehicle's battery degradation 30 days ahead — not just today's state. Four services: a fleet simulator, a
Kafka ingestion pipeline, a machine learning model, and a secured dashboard — all working together, all real,
all tested."

---

## 1:00–3:00 — Live Demo (the longest segment — show it actually working)

**Step 1 (~20s): Login**
- Navigate to `http://localhost:3000`
- Log in as `manager@cvi.local` / `manager123`
- Say: "This is the Fleet Manager role — can view everything and act on alerts."

**Step 2 (~30s): Fleet Overview**
- Point at the live numbers: vehicle count, average SOH, open alerts count.
- Say: "This is live — pushed over a WebSocket every couple seconds, not a static page."
- Point at the archetype breakdown chart.

**Step 3 (~40s): Risk & ML page**
- Click into Risk & ML.
- Say: "Here's the real output of our model — 112 vehicles LOW risk, 69 MEDIUM, 19 HIGH, out of 200. This
  wasn't trivial to get right — we had to recalibrate the thresholds once against real prediction data,
  because our first guess made everything read LOW. That's documented honestly in our Solution Document, not
  hidden."
- Click "Run Prediction" or show the HIGH-risk list.

**Step 4 (~40s): Vehicle Detail**
- Search for your chosen BAD-archetype / HIGH-risk vehicle.
- Open its detail page — show SOC/SOH history chart and its prediction.
- Say: "This vehicle is flagged HIGH risk — you can see its degradation trend and the model's actual
  predicted number, not just a label."

**Step 5 (~20-30s, optional if time allows): Trigger live simulation**
- Switch to terminal, run the pre-typed `curl` to start a small simulation.
- Switch back to Simulation Control page, show progress bar/ETA ticking up live.
- Say: "This is a live run of our own fleet simulator — fully deterministic and reproducible, same seed
  always gives the same fleet."

---

## 3:00–4:15 — Under the Hood (architecture, scale, one failure-recovery moment)

**Show:** architecture diagram again, or ER diagram.

**Say (~30s):**
"Under the hood: Kafka for durable, partitioned event streaming; PostgreSQL for the relational core and
predictions; MongoDB for raw telemetry; Redis for live state. Each store earns its place — we don't just
throw everything into one database."

**Show k6 result file or just state the numbers (~30s):**
"We load-tested the API with 200 concurrent simulated users — p95 latency was 60 milliseconds, p99 was 93
milliseconds, both well under our targets, 100% success rate on over 16,000 requests."

**Show chaos demo result or screen-record a live re-run of `chaos_demo.ps1` if time allows (~30-45s):**
"And we didn't just design for resilience, we tested it — we killed the ingestion service mid-stream to
simulate a real crash, and it recovered automatically in under 6 seconds with zero data loss. That's a real
measured result, not a claim."

**If the 100K throughput number is ready by recording time, say it here too** — otherwise skip this line.

---

## 4:15–5:00 — Impact & Next Steps

**Say:**
"Bottom line: our model beats a naive baseline by 49% — real, measured, not assumed. We know exactly what's
left to harden this for production: multi-replica databases for full availability, stronger auth with mTLS,
and scaling ingestion horizontally across our already-partitioned Kafka topic. We've documented all of that
honestly rather than hiding the gaps."

**Introduce the team, thank the viewer, end.**

---

## After recording

- Upload (unlisted YouTube/Vimeo/Drive), confirm it's viewable via the link without extra permissions needed.
- Paste the URL into the Solution Document's §13 (and the cover page's Demo Video URL field).
- Re-export the Solution Document to PDF once that link (and the two pending numbers) are in.
