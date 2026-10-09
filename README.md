# BhuRaksha — Connected Slope Intelligence

**SIH26001 · Landslide risk monitoring and early-warning platform for Northeast India**

BhuRaksha turns a laptop into a complete landslide early-warning system for a district:
a Flask + SQLite backend with a **Belief Fusion Engine**, a React **command center** for
the district control room, and **five role-specific mobile apps** that field teams open
in a phone browser. A citizen submits a report with a photograph from their phone; the
laptop collects evidence (rainfall, terrain, sensors, reports, satellite status), fuses it
into a risk score with a full audit trace, propagates context across connected slopes, and
raises a dedupe-safe alert that the response team acknowledges, dispatches against, and
resolves — every step persisted in the same database.

---

## Quick start (Windows / PowerShell)

```powershell
.\setup.ps1          # python deps + npm builds + DB seed + ML model + tests
.\run_backend.ps1    # starts the server and prints the phone URLs
```

Then open:

| Who | URL |
|---|---|
| Control room (laptop) | `http://<LAPTOP_LAN_IP>:5000/` |
| Mobile apps launcher | `http://<LAPTOP_LAN_IP>:5000/app/` |
| Citizen app | `http://<LAPTOP_LAN_IP>:5000/app/citizen.html` |
| API / health | `http://<LAPTOP_LAN_IP>:5000/api/system/info` |

Phones must use the **LAN IP printed at startup** (never `localhost`) and be on the same
Wi-Fi. `run_backend.ps1` prints the exact URLs; `/api/system/info` reports the detected
LAN IP as well.

Manual equivalent (if you prefer not to use the scripts):

```powershell
pip install -r requirements.txt
cd frontend; npm install; npm run build; cd ..
cd mobile;   npm install; npm run build; cd ..
python backend\app.py                 # auto-seeds, trains, assesses on first run
```

### Demo accounts

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Citizen | `citizen` | `citizen123` |
| Field volunteer | `volunteer` | `volunteer123` |
| Community leader | `leader` | `leader123` |
| Response operator | `response` | `response123` |
| Analyst | `analyst` | `analyst123` |

---

## The 3-minute live demo

**One server, five phones (or browser tabs), one shared database.**

1. **Laptop** — `.\run_backend.ps1`, log in to the command center as `analyst`.
   The dashboard shows 77 slopes across 26 NE-India clusters with current bands.
2. **Phone A (Citizen app)** — log in as `citizen`, tap *Submit report*: title, category,
   a real photo from the phone camera, GPS fix from `navigator.geolocation`.
   The report is matched to the nearest slope and the **analysis pipeline runs on the
   laptop for that slope**; the response shows score, band, recommendation and the
   assessment id.
3. **Phone B (Analyst app)** — log in as `analyst`, *Evidence* tab → open that slope:
   every accepted/rejected evidence row with weights, freshness, dependency adjustments,
   plus the JSON **audit trace** (prior, fusion method, one-hop contributions, caveats).
   *Run* tab → `Run assessment for all slopes`.
4. **Phone C (Response app)** — log in as `response`, *Alerts* tab → **Acknowledge**
   the alert → an incident code is created → open it, dispatch `SDRF Team A` with an ETA,
   move it `dispatched → responding → resolved`, then **Resolve** the alert.
5. **Phone D (Field app)** — log in as `volunteer`, verify a slope as `ground_truth`
   with a photo; that observation re-enters the fusion on the next run.
6. **Phone E (Community app)** — log in as `leader`, view the village risk board,
   acknowledge an alert with a note, broadcast a warning that appears instantly in the
   citizen app's notification tray.
7. **Laptop** — the command center's Alerts/Incidents pages reflect every action through
   5-second polling; the audit log (`/api/audit`) shows who did what.

Reproducible scripted version of the same flow (real HTTP, no mocking):

```powershell
python scripts\demo_e2e.py --base http://127.0.0.1:5000
```

It prints the evidence table, the fusion trace, and the full
report → alert → ack → dispatch → resolve loop.

---

## The two named innovations

### 1. Belief Fusion Engine (`backend/services/belief_fusion.py`)

Every source (rainfall gauge, terrain prior, sensor, citizen report, field observation,
satellite, local ML) contributes evidence, not a decision:

```
effective_weight = source_weight × (confidence × geo_relevance × data_quality) × freshness_factor
freshness_factor = 0.5 ** (age_hours / half_life_hours)          # half-life decay
```

- Evidence is converted to a probability, transformed to **log-odds**, and combined as a
  normalized weighted pool with a documented prior (`p0 = 0.12`, `w0 = 1.0`).
- **Safeguards**: stale rows are rejected (`age > max_age`), zero-weight sources can never
  move the score, duplicate/contradictory inputs are flagged, missing inputs are listed
  instead of silently zeroed, and only a *ground-truth* field observation can put a slope
  in the `CONFIRMED` band.
- **Dependency discounting**: correlated evidence (e.g. same rainstorm read by several
  sources) shares one group; the strongest member keeps full weight, the rest are scaled
  by `ρ = 0.55`, so one event is never double-counted.
- Output: score, confidence, band, flags, missing inputs, recommendation — plus a
  **JSON audit trace** (`GET /api/assessments/<id>/trace`) showing every weight, reason
  and caveat, so the risk state is explainable rather than a black box.

### 2. Connected-Slope Intelligence (`backend/services/connected_slope.py`)

Slopes are nodes in a documented graph (`spatial_adjacency`, `rainfall_similarity`,
`geology_similarity`, `hydrology_downslope` built by `backend/gis/graph_builder.py`).
A slope's assessed neighbours can raise — never lower — its score through **one-hop,
bounded** influence:

```
inherited_influence = Σ (strength × distance_decay × freshness × confidence) × source_confidence
uplift              = min(CONTRIBUTION × TRANSFER_COEFF, MAX_UPLIFT)   # hard cap 0.15
```

- Edge basis is recorded per contribution (`nearest-gauge 24h rainfall differs by …`),
  hydrology edges are elevation-gradient approximations flagged `validated=false`.
- Context **never confirms a landslide on its own** — it only nudges risk within the cap
  and is always labelled as inherited in the UI and the trace.

### Supporting pieces

- **Alert dedupe**: `dedupe_key = slope|band|hour` (unique index) — re-running the
  pipeline or a phone re-submitting never floods the response team.
- **Controlled incident transitions**: a state machine (`reported → verifying →
  acknowledged → dispatched → responding → resolved → closed`) with RBAC on every edge.
- **Local ML** (`backend/ml/`): RandomForest susceptibility model trained and inferred
  in-process — no external model API.
- **Tiles**: OpenStreetMap with an offline cache and graceful placeholder
  (`backend/api/tiles_api.py`), so the demo survives no-internet rooms.

---

## Project structure

```
backend/
  app.py                 Flask factory, SPA/mobile serving, auto-bootstrap
  config.py db.py schema.sql auth.py seed.py
  api/                   auth, slopes, reports(+observations), assessments, alerts,
                         incidents(+dispatch), data/ML, system(+broadcast), tiles
  services/              belief_fusion, evidence, connected_slope, risk_state,
                         pipeline, data_quality        ← the engine
  gis/                   geo, graph_builder
  ml/                    train, predict (local RandomForest)
  tests/                 pytest suite (53 tests)
frontend/                command-center SPA (React 19 + Vite), 9 pages, Leaflet + Recharts
mobile/                  five mobile SPAs in one project (Vite multi-entry):
                         citizen, field, community, response, analyst
scripts/demo_e2e.py      reproducible end-to-end demo over real HTTP
setup.ps1  run_backend.ps1
```

API surface (all JSON, bearer-token auth, RBAC enforced per permission):

```
POST /api/auth/login|register   GET /api/auth/me|users
GET  /api/slopes  /api/slopes/<id>  /api/slopes/<id>/history  /api/graph
POST /api/reports (multipart, photo)  GET /api/reports  POST /api/reports/<id>/verify
POST /api/observations  GET /api/observations  GET /api/tasks/verification
POST /api/assessments/run  GET /api/assessments/<id>/trace
GET  /api/alerts  POST /api/alerts/<id>/ack|resolve
GET  /api/incidents  POST /api/incidents/<id>/events|dispatch
GET  /api/rainfall|sensors|satellite|data-quality  /api/ml/model-info|train
GET  /api/dashboard  /api/audit  /api/notifications  POST /api/notifications/broadcast
GET  /api/tiles/<z>/<x>/<y>.png
```

Frontend dev loop: `cd frontend && npm run dev` (Vite proxies `/api` to `:5000`).
Mobile dev loop: `cd mobile && npm run dev` → `http://localhost:5174/app/citizen.html`.

---

## Testing

```powershell
python -m pytest backend\tests -q          # 53 passed, 1 skipped
python scripts\demo_e2e.py                 # full workflow over live HTTP
```

Coverage includes the fusion maths (weights, freshness, dependency discount, band
transitions), connected-slope caps, the report→alert→incident API flow, RBAC denials,
alert dedupe, the observation round-trip, and the serving layer.

---

## Honest limitations — read before presenting

- **Rainfall, sensor and terrain inputs are prototype fixtures** (`data_mode =
  prototype_fixture`): physically plausible synthetic series for 23 stations, not a live
  IMD/NDMA feed. Sources are labelled as fixtures in the UI, API payloads and traces.
- **Satellite is unavailable in this build.** Every satellite row is recorded as
  `status='unavailable'` and appears under `missing_inputs` — never as fabricated data.
- **The ML model is trained on a synthetic, physically-informed dataset**
  (`synthetic_prototype`, documented generating process in `backend/ml/train.py`).
  Measured metrics: accuracy 0.73, ROC AUC 0.79 (5-fold CV 0.77 ± 0.01) **on that
  synthetic data only** — not field-validated accuracy.
- **77 slopes / 26 clusters are fixture geometry** for the demo, not a surveyed
  landslide inventory; graph edges are computed from that geometry, and hydrology edges
  are elevation-gradient estimates marked unvalidated.
- **CONFIRMED requires human ground truth** — the system cannot confirm a landslide from
  remote/fixture evidence alone.
- Not production-hardened: dev-grade secret key default, no HTTPS, no rate limiting,
  single-node SQLite. See `.env.example` for the settings you would change first.

---

## Configuration

Copy `.env.example` → `backend/.env` (or set shell variables). Key values:

| Variable | Default | Meaning |
|---|---|---|
| `BHURAKSHA_HOST` / `BHURAKSHA_PORT` | `0.0.0.0` / `5000` | bind address (0.0.0.0 = LAN reachable) |
| `BHURAKSHA_SECRET_KEY` | dev value | **change** before any real deployment |
| `BHURAKSHA_DB_PATH` | `backend/data/bhuraksha.db` | SQLite location |
| `BHURAKSHA_CORS_ORIGINS` | `*` | allowed origins for the API |
| `VITE_API_BASE_URL` | `/api` | build-time API base for the two SPAs |

**Reset to factory state:** stop the server, delete `backend/data/bhuraksha.db*` and
`backend/ml/models/latest.*`, then run `python backend\app.py` again.
