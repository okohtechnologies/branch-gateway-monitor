CREATE TABLE IF NOT EXISTS branches (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    host            TEXT NOT NULL,
    location        TEXT NOT NULL DEFAULT '',
    probe_type      TEXT NOT NULL DEFAULT 'icmp' CHECK (probe_type IN ('icmp', 'tcp')),
    port            INTEGER,
    latency_warn_ms DOUBLE PRECISION NOT NULL DEFAULT 200,
    loss_warn_pct   DOUBLE PRECISION NOT NULL DEFAULT 20,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE
);

-- branch_id is NULL for canary probes.
CREATE TABLE IF NOT EXISTS checks (
    id             BIGSERIAL PRIMARY KEY,
    branch_id      INTEGER REFERENCES branches(id) ON DELETE CASCADE,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    avg_latency_ms DOUBLE PRECISION,
    loss_pct       DOUBLE PRECISION NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('UP', 'DEGRADED', 'DOWN'))
);
CREATE INDEX IF NOT EXISTS checks_branch_ts ON checks (branch_id, ts DESC);

CREATE TABLE IF NOT EXISTS incidents (
    id              SERIAL PRIMARY KEY,
    branch_id       INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    severity        TEXT NOT NULL CHECK (severity IN ('DEGRADED', 'DOWN')),
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    acknowledged_by TEXT,
    acknowledged_at TIMESTAMPTZ
);
-- At most one open incident per branch.
CREATE UNIQUE INDEX IF NOT EXISTS incidents_one_open
    ON incidents (branch_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS notifications (
    id          SERIAL PRIMARY KEY,
    incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL,
    channel     TEXT NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    success     BOOLEAN NOT NULL,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS mutes (
    id         SERIAL PRIMARY KEY,
    branch_id  INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    until      TIMESTAMPTZ NOT NULL,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mutes_branch_until ON mutes (branch_id, until);

-- A muted notification is recorded but never sent.
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS suppressed BOOLEAN NOT NULL DEFAULT FALSE;
