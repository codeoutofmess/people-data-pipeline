-- Every table for the project. Safe to run more than once (IF NOT EXISTS).
-- shortcut: no migration tool; changing a column means dropping the database. Add Alembic or similar if the schema needs to evolve.
-- Pydantic validation in the pipeline is the main gate (it records a reason for each bad row).
-- The constraints here are a second safety net in case bad data gets past it.

-- One row per person. The only table holding personal details (name, email),
-- so the retention command has a single place to blank them.
CREATE TABLE IF NOT EXISTS people (
    person_id   text PRIMARY KEY,
    full_name   text,                -- nullable: retention blanks it
    email       text,                -- nullable: retention blanks it
    redacted_at timestamptz          -- set when retention blanked this row
);

-- Hiring: one application per person in this demo.
CREATE TABLE IF NOT EXISTS applications (
    application_id text PRIMARY KEY,
    person_id      text NOT NULL REFERENCES people (person_id),
    source_channel text NOT NULL,
    stage          text NOT NULL,
    hired          boolean NOT NULL,
    applied_on     date NOT NULL,
    decided_on     date              -- empty while the application is still open
);

-- HR record: only hired people become employees.
CREATE TABLE IF NOT EXISTS employees (
    person_id  text PRIMARY KEY REFERENCES people (person_id),
    team       text NOT NULL,
    role       text NOT NULL,
    -- DEFERRABLE: the manager check runs at commit, so a file can list a report before their manager.
    manager_id text REFERENCES employees (person_id) DEFERRABLE INITIALLY DEFERRED,
    start_date date NOT NULL,
    status     text NOT NULL
);

-- Payroll: one row per person per monthly pay period. Amounts are synthetic.
CREATE TABLE IF NOT EXISTS pay_records (
    person_id    text NOT NULL REFERENCES employees (person_id),
    period_start date NOT NULL,
    period_end   date NOT NULL,
    gross_amount numeric(10, 2) NOT NULL CHECK (gross_amount >= 0),
    PRIMARY KEY (person_id, period_start)
);

-- Performance reviews.
CREATE TABLE IF NOT EXISTS reviews (
    review_id   text PRIMARY KEY,
    person_id   text NOT NULL REFERENCES employees (person_id),
    cycle       text NOT NULL,
    rating      smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
    reviewer_id text NOT NULL REFERENCES employees (person_id),
    reviewed_on date NOT NULL
);

-- One row per pipeline run, with its data-quality summary.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    summary     jsonb
);

-- Rows the pipeline rejected, kept with the reason so someone can fix the source.
CREATE TABLE IF NOT EXISTS quarantine (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     bigint NOT NULL REFERENCES pipeline_runs (run_id),
    source     text NOT NULL,        -- which file, e.g. 'payroll'
    row_number integer NOT NULL,     -- line in the file (header is line 1)
    raw        jsonb NOT NULL,       -- the row as read, minus name and email
    reason     text NOT NULL
);

-- Demo API users. Only a SHA-256 hash of each token is stored, never the token itself.
CREATE TABLE IF NOT EXISTS api_users (
    username     text PRIMARY KEY,
    role         text NOT NULL CHECK (role IN ('people_admin', 'manager', 'viewer', 'employee')),
    token_sha256 text NOT NULL UNIQUE,
    person_id    text REFERENCES employees (person_id), -- managers and employees: who they are
    expires_at   timestamptz NOT NULL                   -- after this, the token is refused
);

-- Every read of person data, allowed or denied, and every failed login.
-- No foreign key on person_id: an attempt to read a person who does not exist should still be logged.
CREATE TABLE IF NOT EXISTS audit_log (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    username  text NOT NULL,
    role      text NOT NULL,
    action    text NOT NULL,         -- 'read_journey', 'read_pay' or 'failed_login'
    person_id text,
    outcome   text NOT NULL CHECK (outcome IN ('allowed', 'denied')),
    reason    text                   -- why a person's record was opened; empty for failed logins
);

-- The audit log is append-only: rows can be added but never changed or deleted.
-- shortcut: a database owner can still drop these triggers. For real tamper-proofing, also give the
-- app an insert-only database role and copy the log somewhere the app cannot write.
CREATE OR REPLACE FUNCTION audit_log_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP;
END $$;

CREATE OR REPLACE TRIGGER audit_log_no_update_or_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

CREATE OR REPLACE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_is_append_only();
