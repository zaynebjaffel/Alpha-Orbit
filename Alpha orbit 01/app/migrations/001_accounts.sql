CREATE TABLE users (
    id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    country TEXT NOT NULL,
    phone TEXT,
    profile_picture TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX sessions_expiry_idx ON sessions(expires_at);

CREATE TABLE farms (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    farm_name TEXT NOT NULL,
    geojson TEXT NOT NULL,
    centroid_latitude REAL NOT NULL,
    centroid_longitude REAL NOT NULL,
    area_hectares REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, user_id)
);
CREATE INDEX farms_user_created_idx ON farms(user_id, created_at);

CREATE TABLE farm_analyses (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    farm_id TEXT NOT NULL,
    selected_crop TEXT,
    analysis_results TEXT NOT NULL,
    climate_scenario TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (farm_id, user_id) REFERENCES farms(id, user_id) ON DELETE CASCADE
);
CREATE INDEX analyses_user_created_idx ON farm_analyses(user_id, created_at);

CREATE TABLE password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX password_resets_expiry_idx ON password_resets(expires_at);

CREATE TABLE auth_rate_limits (
    rate_key TEXT PRIMARY KEY,
    window_started INTEGER NOT NULL,
    attempts INTEGER NOT NULL
);
