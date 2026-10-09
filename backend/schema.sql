PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  username          TEXT UNIQUE NOT NULL,
  password_hash     TEXT NOT NULL,
  display_name      TEXT NOT NULL,
  role              TEXT NOT NULL CHECK (role IN ('citizen','field_volunteer','community_leader','response_operator','analyst','admin')),
  phone             TEXT,
  village           TEXT,
  district          TEXT,
  state             TEXT,
  lat               REAL,
  lng               REAL,
  active            INTEGER NOT NULL DEFAULT 1,
  created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token             TEXT PRIMARY KEY,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at        TEXT NOT NULL,
  expires_at        TEXT NOT NULL
);

-- Registry of evidence sources: reliability weight, freshness model, availability.
CREATE TABLE IF NOT EXISTS data_sources (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  code                  TEXT UNIQUE NOT NULL,
  name                  TEXT NOT NULL,
  kind                  TEXT NOT NULL,
  source_weight         REAL NOT NULL,
  freshness_half_life_h REAL NOT NULL,
  max_age_h             REAL NOT NULL,
  data_mode             TEXT NOT NULL DEFAULT 'prototype_fixture',
  status                TEXT NOT NULL DEFAULT 'available',
  notes                 TEXT
);

CREATE TABLE IF NOT EXISTS slopes (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  code                TEXT UNIQUE NOT NULL,
  name                TEXT NOT NULL,
  lat                 REAL NOT NULL,
  lng                 REAL NOT NULL,
  elevation_m         REAL,
  slope_angle_deg     REAL,
  aspect_deg          REAL,
  curvature           REAL,
  area_km2            REAL,
  lithology           TEXT,
  land_use            TEXT,
  road_cut            INTEGER NOT NULL DEFAULT 0,
  vegetation          REAL,
  drainage_density    REAL,
  district            TEXT,
  state               TEXT,
  cluster             TEXT,
  historical_incidents INTEGER NOT NULL DEFAULT 0,
  condition_status    TEXT NOT NULL DEFAULT 'unknown',
  catchment_elev_m    REAL,
  notes               TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slope_edges (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  src_slope_id   INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  dst_slope_id   INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  rel_type       TEXT NOT NULL CHECK (rel_type IN ('spatial_adjacency','rainfall_similarity','geology_similarity','hydrology_downslope')),
  strength       REAL NOT NULL,
  distance_km    REAL,
  directionality TEXT NOT NULL DEFAULT 'symmetric',
  confidence     REAL NOT NULL DEFAULT 0.7,
  basis          TEXT NOT NULL,
  validated      INTEGER NOT NULL DEFAULT 0,
  meta_json      TEXT,
  created_at     TEXT NOT NULL,
  UNIQUE (src_slope_id, dst_slope_id, rel_type)
);

CREATE TABLE IF NOT EXISTS rainfall_observations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  station_code  TEXT NOT NULL,
  station_name  TEXT NOT NULL,
  lat           REAL NOT NULL,
  lng           REAL NOT NULL,
  observed_at   TEXT NOT NULL,
  mm_24h        REAL,
  mm_72h        REAL,
  source_code   TEXT NOT NULL,
  quality       REAL NOT NULL DEFAULT 1.0,
  data_mode     TEXT NOT NULL DEFAULT 'prototype_fixture',
  ingested_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rainfall_obs ON rainfall_observations (station_code, observed_at DESC);

CREATE TABLE IF NOT EXISTS sensor_readings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slope_id    INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  sensor_uid  TEXT NOT NULL,
  sensor_type TEXT NOT NULL CHECK (sensor_type IN ('inclinometer','piezometer','crack_gauge','soil_moisture')),
  observed_at TEXT NOT NULL,
  value       REAL NOT NULL,
  unit        TEXT NOT NULL,
  quality     REAL NOT NULL DEFAULT 1.0,
  data_mode   TEXT NOT NULL DEFAULT 'prototype_fixture',
  ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sensor_slope ON sensor_readings (slope_id, observed_at DESC);

-- Satellite feed is wired into the pipeline but has no live provider in this build.
CREATE TABLE IF NOT EXISTS satellite_observations (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slope_id    INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  observed_at TEXT,
  product     TEXT NOT NULL,
  index_name  TEXT NOT NULL,
  index_value REAL,
  provider    TEXT,
  status      TEXT NOT NULL DEFAULT 'unavailable',
  data_mode   TEXT NOT NULL DEFAULT 'unavailable',
  detail      TEXT,
  ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citizen_reports (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  report_code    TEXT UNIQUE NOT NULL,
  slope_id       INTEGER REFERENCES slopes(id) ON DELETE SET NULL,
  reporter_user  INTEGER REFERENCES users(id) ON DELETE SET NULL,
  title          TEXT NOT NULL,
  description    TEXT,
  category       TEXT NOT NULL CHECK (category IN ('crack','erosion','debris_flow','subsidence','water_seepage','rockfall','other')),
  observed_at    TEXT NOT NULL,
  lat            REAL NOT NULL,
  lng            REAL NOT NULL,
  gps_source     TEXT NOT NULL CHECK (gps_source IN ('device','manual','fallback_configured','unavailable')),
  photo_path     TEXT,
  photo_hash     TEXT,
  photo_origin   TEXT,
  severity_hint  TEXT,
  status         TEXT NOT NULL DEFAULT 'received',
  cluster_id     TEXT,
  is_duplicate   INTEGER NOT NULL DEFAULT 0,
  duplicate_of   INTEGER REFERENCES citizen_reports(id) ON DELETE SET NULL,
  data_mode      TEXT NOT NULL DEFAULT 'live',
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_slope ON citizen_reports (slope_id, created_at DESC);

CREATE TABLE IF NOT EXISTS field_observations (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id          INTEGER REFERENCES citizen_reports(id) ON DELETE SET NULL,
  slope_id           INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  verifier_user      INTEGER REFERENCES users(id) ON DELETE SET NULL,
  verification_status TEXT NOT NULL CHECK (verification_status IN ('ground_truth','inconclusive','not_verified')),
  crack_mm           REAL,
  seepage            INTEGER NOT NULL DEFAULT 0,
  fresh_scarp        INTEGER NOT NULL DEFAULT 0,
  displacement_m     REAL,
  material           TEXT,
  notes              TEXT,
  photo_path         TEXT,
  photo_origin       TEXT,
  lat                REAL,
  lng                REAL,
  gps_source         TEXT CHECK (gps_source IN ('device','manual','fallback_configured','unavailable')),
  observed_at        TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fieldobs_slope ON field_observations (slope_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS assessments (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  slope_id          INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  method            TEXT NOT NULL,
  engine_version    TEXT NOT NULL,
  prior_probability REAL NOT NULL,
  fused_probability REAL NOT NULL,
  direct_score      REAL NOT NULL,
  inherited_influence REAL NOT NULL DEFAULT 0,
  uplift            REAL NOT NULL DEFAULT 0,
  final_score       REAL NOT NULL,
  risk_band         TEXT NOT NULL,
  confidence        REAL NOT NULL,
  ground_truth      INTEGER NOT NULL DEFAULT 0,
  accepted_count    INTEGER NOT NULL DEFAULT 0,
  rejected_count    INTEGER NOT NULL DEFAULT 0,
  missing_inputs    TEXT,
  recommendation    TEXT,
  next_action       TEXT,
  flags             TEXT,
  trace_json        TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assess_slope ON assessments (slope_id, created_at DESC);

CREATE TABLE IF NOT EXISTS assessment_evidence (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  assessment_id        INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
  evidence_type        TEXT NOT NULL,
  source_code          TEXT NOT NULL,
  label                TEXT,
  source_weight        REAL NOT NULL,
  confidence           REAL NOT NULL,
  adjusted_confidence  REAL,
  freshness_factor     REAL,
  effective_weight     REAL NOT NULL DEFAULT 0,
  probability          REAL,
  observed_at          TEXT,
  geo_relevance        REAL NOT NULL DEFAULT 1.0,
  data_quality         REAL NOT NULL DEFAULT 1.0,
  dependency_group     TEXT,
  dependency_adjustment TEXT,
  status               TEXT NOT NULL CHECK (status IN ('accepted','rejected','missing','duplicate')),
  reason               TEXT,
  provenance           TEXT,
  detail_json          TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_assessment ON assessment_evidence (assessment_id);

CREATE TABLE IF NOT EXISTS slope_risk_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  slope_id      INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  risk_band     TEXT NOT NULL,
  probability   REAL NOT NULL,
  assessment_id INTEGER REFERENCES assessments(id) ON DELETE SET NULL,
  from_band     TEXT,
  allowed       INTEGER NOT NULL DEFAULT 1,
  changed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_riskhist_slope ON slope_risk_history (slope_id, changed_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_code     TEXT UNIQUE NOT NULL,
  slope_id       INTEGER NOT NULL REFERENCES slopes(id) ON DELETE CASCADE,
  assessment_id  INTEGER REFERENCES assessments(id) ON DELETE SET NULL,
  severity       TEXT NOT NULL CHECK (severity IN ('high','very_high','confirmed')),
  band           TEXT NOT NULL,
  title          TEXT NOT NULL,
  message        TEXT NOT NULL,
  dedupe_key     TEXT UNIQUE NOT NULL,
  status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','dispatched','resolved','suppressed')),
  scope_state    TEXT,
  scope_district TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  acknowledged_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  acknowledged_at TEXT,
  resolved_at    TEXT,
  suppressed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts (status, created_at DESC);

CREATE TABLE IF NOT EXISTS alert_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id   INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
  user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  action     TEXT NOT NULL,
  note       TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_code  TEXT UNIQUE NOT NULL,
  alert_id       INTEGER REFERENCES alerts(id) ON DELETE SET NULL,
  slope_id       INTEGER REFERENCES slopes(id) ON DELETE SET NULL,
  report_id      INTEGER REFERENCES citizen_reports(id) ON DELETE SET NULL,
  title          TEXT NOT NULL,
  description    TEXT,
  severity       TEXT NOT NULL CHECK (severity IN ('low','moderate','high','very_high','confirmed')),
  status         TEXT NOT NULL DEFAULT 'reported' CHECK (status IN ('reported','verifying','acknowledged','dispatched','responding','resolved','closed')),
  lat            REAL,
  lng            REAL,
  district       TEXT,
  state          TEXT,
  data_mode      TEXT NOT NULL DEFAULT 'live',
  created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  resolved_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status, created_at DESC);

CREATE TABLE IF NOT EXISTS incident_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  event_type  TEXT NOT NULL,
  from_status TEXT,
  to_status   TEXT,
  note        TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id  INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  team_name    TEXT NOT NULL,
  personnel    INTEGER,
  eta_minutes  INTEGER,
  status       TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','enroute','on_scene','completed','cancelled')),
  requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  note         TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
  role_scope   TEXT,
  district     TEXT,
  title        TEXT NOT NULL,
  body         TEXT NOT NULL,
  severity     TEXT NOT NULL DEFAULT 'info',
  related_type TEXT,
  related_id   INTEGER,
  read         INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ml_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  model_name  TEXT NOT NULL,
  version     TEXT NOT NULL,
  trained_at  TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  n_samples   INTEGER NOT NULL,
  features    TEXT NOT NULL,
  data_mode   TEXT NOT NULL,
  artifact_path TEXT,
  notes       TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  action      TEXT NOT NULL,
  entity      TEXT,
  entity_id   INTEGER,
  detail_json TEXT,
  created_at  TEXT NOT NULL
);

-- Additional photographs / videos attached to one citizen report (2-3 captures).
CREATE TABLE IF NOT EXISTS report_media (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id   INTEGER NOT NULL REFERENCES citizen_reports(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN ('photo','video')),
  path        TEXT NOT NULL,
  sha256      TEXT,
  origin      TEXT,
  bytes       INTEGER,
  mime        TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_media ON report_media(report_id);

-- Automated analysis job: one row per run, stages persisted individually.
CREATE TABLE IF NOT EXISTS analysis_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id       INTEGER NOT NULL REFERENCES citizen_reports(id) ON DELETE CASCADE,
  slope_id        INTEGER REFERENCES slopes(id) ON DELETE SET NULL,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','failed')),
  idempotency_key TEXT UNIQUE NOT NULL,
  attempt         INTEGER NOT NULL DEFAULT 1,
  assessment_id   INTEGER REFERENCES assessments(id) ON DELETE SET NULL,
  alert_id        INTEGER REFERENCES alerts(id) ON DELETE SET NULL,
  started_at      TEXT,
  finished_at     TEXT,
  error           TEXT,
  created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_report ON analysis_jobs(report_id, created_at DESC);

CREATE TABLE IF NOT EXISTS analysis_stages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id       INTEGER NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
  stage        TEXT NOT NULL,
  seq          INTEGER NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('pending','running','ok','failed','skipped')),
  outcome      TEXT,
  explanation  TEXT,
  error        TEXT,
  started_at   TEXT,
  finished_at  TEXT,
  UNIQUE (job_id, stage)
);

-- Device registry for the sensor/monitoring operations app.
CREATE TABLE IF NOT EXISTS sensors (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  device_uid    TEXT UNIQUE NOT NULL,
  slope_id      INTEGER REFERENCES slopes(id) ON DELETE SET NULL,
  sensor_type   TEXT NOT NULL CHECK (sensor_type IN ('inclinometer','piezometer','crack_gauge','soil_moisture','rain_gauge','gateway')),
  measured_vars TEXT,
  vendor        TEXT,
  installed_at  TEXT,
  battery_pct   REAL,
  power_source  TEXT,
  health        TEXT NOT NULL DEFAULT 'unknown' CHECK (health IN ('ok','warning','stale','fault','unknown')),
  last_comm_at  TEXT,
  last_obs_at   TEXT,
  data_mode     TEXT NOT NULL DEFAULT 'prototype_fixture',
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired','fault')),
  note          TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sensors_slope ON sensors(slope_id);

CREATE TABLE IF NOT EXISTS sensor_maintenance (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  sensor_id    INTEGER NOT NULL REFERENCES sensors(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('calibration','maintenance','repair','note')),
  performed_at TEXT NOT NULL,
  performed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  notes        TEXT,
  result       TEXT,
  created_at   TEXT NOT NULL
);

-- Ingestion / processing errors per data source (visible in Data & System Status).
CREATE TABLE IF NOT EXISTS ingestion_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_code TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('ok','error','degraded','skipped')),
  detail      TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingestion ON ingestion_log(source_code, created_at DESC);
