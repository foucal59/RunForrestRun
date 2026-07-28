-- SQLite schema — local development mirror of the Neon PostgreSQL schema.
-- Usage:
--   sqlite3 .runtime/strava.db < schema_sqlite.sql
-- Or automatically applied by db_sqlite.py on first connect.

CREATE TABLE IF NOT EXISTS athletes (
    id INTEGER PRIMARY KEY,
    firstname TEXT,
    lastname TEXT,
    city TEXT,
    country TEXT,
    sex TEXT,
    weight REAL,
    profile_url TEXT
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    athlete_id INTEGER NOT NULL,
    name TEXT,
    start_date TEXT,
    start_date_local TEXT,
    timezone TEXT,
    utc_offset INTEGER,
    distance REAL DEFAULT 0,
    moving_time INTEGER DEFAULT 0,
    elapsed_time INTEGER DEFAULT 0,
    total_elevation_gain REAL DEFAULT 0,
    average_speed REAL DEFAULT 0,
    max_speed REAL DEFAULT 0,
    average_heartrate REAL,
    max_heartrate INTEGER,
    map_summary_polyline TEXT,
    map_polyline TEXT,
    gear_id TEXT,
    sport_type TEXT DEFAULT 'Run',
    type TEXT DEFAULT 'Run',
    start_lat REAL,
    start_lng REAL,
    end_lat REAL,
    end_lng REAL,
    pr_count INTEGER DEFAULT 0,
    suffer_score INTEGER,
    calories REAL,
    workout_type INTEGER,
    manual INTEGER DEFAULT 0,
    private INTEGER DEFAULT 0,
    has_heartrate INTEGER DEFAULT 0,
    average_cadence REAL,
    average_watts REAL,
    weighted_average_watts REAL,
    max_watts INTEGER,
    kudos_count INTEGER,
    achievement_count INTEGER,
    average_temp REAL,
    elev_high REAL,
    elev_low REAL,
    description TEXT,
    device_name TEXT,
    details_fetched_at TEXT,
    -- Parité avec les colonnes de réplication Postgres : l'upsert d'activités
    -- les référence dans sa clause ON CONFLICT, SQLite exige donc leur présence.
    sync_complete_at TEXT,
    sync_status TEXT NOT NULL DEFAULT 'partial',
    source TEXT DEFAULT 'garmin',
    garmin_activity_id INTEGER,
    garmin_activity_uuid TEXT,
    garmin_timezone_id INTEGER,
    garmin_device_id INTEGER,
    lap_count INTEGER,
    elevation_loss REAL,
    max_cadence REAL,
    aerobic_training_effect REAL,
    anaerobic_training_effect REAL,
    activity_training_load REAL,
    vo2max REAL,
    training_effect_label TEXT,
    avg_stride_length REAL,
    avg_ground_contact_time REAL,
    avg_vertical_oscillation REAL,
    avg_vertical_ratio REAL,
    avg_grade_adjusted_speed REAL,
    body_battery_delta INTEGER,
    steps INTEGER,
    moderate_intensity_minutes INTEGER,
    vigorous_intensity_minutes INTEGER,
    min_temperature REAL,
    max_temperature REAL,
    avg_respiration_rate REAL,
    min_respiration_rate REAL,
    max_respiration_rate REAL,
    water_estimated REAL,
    garmin_workout_id INTEGER,
    garmin_course_id INTEGER,
    hr_time_in_zones TEXT,
    power_time_in_zones TEXT,
    garmin_fastest_splits TEXT,
    garmin_summary TEXT,
    run_metrics_updated_at TEXT,
    run_summary_updated_at TEXT,
    run_zones_updated_at TEXT,
    run_details_updated_at TEXT,
    run_laps_updated_at TEXT,
    run_streams_updated_at TEXT,
    health_snapshot_at TEXT,
    health_sleep_date TEXT,
    health_sleep_score INTEGER,
    health_sleep_quality TEXT,
    health_sleep_duration_seconds INTEGER,
    health_sleep_start_local TEXT,
    health_sleep_end_local TEXT,
    health_hrv_date TEXT,
    health_hrv_last_night_avg_ms REAL,
    health_hrv_weekly_avg_ms REAL,
    health_hrv_status TEXT,
    health_hrv_baseline_low_ms REAL,
    health_hrv_baseline_high_ms REAL,
    health_resting_hr_date TEXT,
    health_resting_hr_bpm INTEGER,
    health_resting_hr_7d_avg_bpm REAL,
    run_health_updated_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS activity_best_efforts (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    name TEXT,
    distance REAL,
    moving_time INTEGER,
    elapsed_time INTEGER
);

CREATE TABLE IF NOT EXISTS activity_splits (
    activity_id INTEGER NOT NULL,
    split_index INTEGER NOT NULL,
    split_type TEXT DEFAULT 'metric',
    distance REAL,
    elapsed_time INTEGER,
    moving_time INTEGER,
    average_speed REAL,
    elevation_difference REAL,
    pace_zone INTEGER,
    UNIQUE(activity_id, split_index, split_type)
);

CREATE TABLE IF NOT EXISTS activity_streams (
    activity_id INTEGER NOT NULL,
    stream_index INTEGER NOT NULL,
    time_sec INTEGER,
    distance REAL,
    lat REAL,
    lng REAL,
    altitude REAL,
    velocity_smooth REAL,
    heartrate REAL,
    cadence REAL,
    watts INTEGER,
    temp REAL,
    moving INTEGER,
    grade_smooth REAL,
    vertical_speed REAL,
    body_battery REAL,
    fractional_cadence REAL,
    grade_adjusted_speed REAL,
    ground_contact_time REAL,
    performance_condition REAL,
    stride_length REAL,
    vertical_oscillation REAL,
    vertical_ratio REAL,
    accumulated_power REAL,
    corrected_altitude REAL,
    uncorrected_altitude REAL,
    garmin_metrics TEXT,
    PRIMARY KEY (activity_id, stream_index)
);

CREATE TABLE IF NOT EXISTS activity_laps (
    id INTEGER UNIQUE,
    activity_id INTEGER NOT NULL,
    lap_index INTEGER NOT NULL,
    name TEXT,
    distance REAL,
    elapsed_time INTEGER,
    moving_time INTEGER,
    start_date TEXT,
    average_speed REAL,
    max_speed REAL,
    average_heartrate REAL,
    max_heartrate REAL,
    average_cadence REAL,
    average_watts REAL,
    total_elevation_gain REAL,
    elevation_loss REAL,
    elev_high REAL,
    elev_low REAL,
    max_vertical_speed REAL,
    start_lat REAL,
    start_lng REAL,
    end_lat REAL,
    end_lng REAL,
    max_cadence REAL,
    max_watts REAL,
    min_watts REAL,
    weighted_average_watts REAL,
    total_work REAL,
    grade_adjusted_speed REAL,
    ground_contact_time REAL,
    stride_length REAL,
    vertical_oscillation REAL,
    vertical_ratio REAL,
    calories REAL,
    bmr_calories REAL,
    intensity_type TEXT,
    workout_step_index INTEGER,
    workout_compliance_score REAL,
    garmin_data TEXT,
    PRIMARY KEY (activity_id, lap_index)
);

CREATE TABLE IF NOT EXISTS athlete_zones (
    zone_type TEXT NOT NULL,
    zone_index INTEGER NOT NULL,
    min_value INTEGER,
    max_value INTEGER,
    PRIMARY KEY (zone_type, zone_index)
);

CREATE TABLE IF NOT EXISTS shoes (
    id TEXT PRIMARY KEY,
    athlete_id INTEGER,
    name TEXT,
    nickname TEXT,
    brand_name TEXT,
    model_name TEXT,
    distance REAL,
    primary_shoe INTEGER DEFAULT 0,
    retired INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS bikes (
    id TEXT PRIMARY KEY,
    athlete_id INTEGER,
    name TEXT,
    brand_name TEXT,
    model_name TEXT,
    distance REAL,
    retired INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vo2max_history (
    date TEXT PRIMARY KEY,
    vo2max REAL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sleep_history (
    date TEXT PRIMARY KEY,
    sleep_score INTEGER,
    sleep_quality TEXT,
    sleep_duration_seconds INTEGER,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_tombstones (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (entity_type, entity_id)
);

CREATE TABLE IF NOT EXISTS athlete_stats (
    athlete_id INTEGER PRIMARY KEY,
    recent_run_totals TEXT,
    all_run_totals TEXT,
    ytd_run_totals TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_activities_run_date
    ON activities (start_date_local DESC)
    WHERE type = 'Run';

CREATE INDEX IF NOT EXISTS idx_activities_athlete
    ON activities (athlete_id);

CREATE INDEX IF NOT EXISTS idx_best_efforts_activity
    ON activity_best_efforts (activity_id);

CREATE INDEX IF NOT EXISTS idx_splits_activity
    ON activity_splits (activity_id);

CREATE INDEX IF NOT EXISTS idx_streams_activity
    ON activity_streams (activity_id);
