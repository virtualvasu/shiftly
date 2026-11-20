-- Every tenant table carries company_id, and every query filters on it.

CREATE TABLE IF NOT EXISTS companies (
    id                     text PRIMARY KEY,
    name                   text NOT NULL,
    timezone               text NOT NULL,
    cutoff_minutes_pickup  int  NOT NULL,
    cutoff_minutes_drop    int  NOT NULL,
    max_seats_per_cab      int  NOT NULL,
    allow_escalation       boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    id          text PRIMARY KEY,
    company_id  text NOT NULL REFERENCES companies(id),
    name        text NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id          text PRIMARY KEY,
    company_id  text NOT NULL REFERENCES companies(id),
    name        text NOT NULL,
    role        text NOT NULL CHECK (role IN ('employee', 'manager')),
    route_id    text REFERENCES routes(id),
    is_demo     boolean NOT NULL DEFAULT false
);

-- One cab run. Occupancy is the count of active roster rows on it.
ALTER TABLE employees ADD COLUMN IF NOT EXISTS email text;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS password_hash text;
CREATE UNIQUE INDEX IF NOT EXISTS employees_email ON employees (lower(email));

CREATE TABLE IF NOT EXISTS trips (
    id                text PRIMARY KEY,
    company_id        text NOT NULL REFERENCES companies(id),
    route_id          text NOT NULL REFERENCES routes(id),
    direction         text NOT NULL CHECK (direction IN ('pickup', 'drop')),
    trip_date         date NOT NULL,
    scheduled_at      timestamptz NOT NULL,
    vehicle_capacity  int  NOT NULL,
    vehicle_no        text NOT NULL
);
CREATE INDEX IF NOT EXISTS trips_lookup
    ON trips (company_id, route_id, direction, trip_date);

CREATE TABLE IF NOT EXISTS roster (
    id           bigserial PRIMARY KEY,
    company_id   text NOT NULL REFERENCES companies(id),
    employee_id  text NOT NULL REFERENCES employees(id),
    trip_date    date NOT NULL,
    direction    text NOT NULL CHECK (direction IN ('pickup', 'drop')),
    trip_id      text NOT NULL REFERENCES trips(id),
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled'))
);
CREATE UNIQUE INDEX IF NOT EXISTS roster_one_active
    ON roster (employee_id, trip_date, direction) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS roster_by_trip ON roster (trip_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS conversations (
    id           text PRIMARY KEY,
    company_id   text NOT NULL REFERENCES companies(id),
    employee_id  text NOT NULL REFERENCES employees(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Display log the UI polls. The chat graph state holds the LLM context.
CREATE TABLE IF NOT EXISTS messages (
    id               bigserial PRIMARY KEY,
    conversation_id  text NOT NULL REFERENCES conversations(id),
    company_id       text NOT NULL REFERENCES companies(id),
    role             text NOT NULL CHECK (role IN ('user', 'assistant')),
    text             text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS shift_change_requests (
    id                  text PRIMARY KEY,
    company_id          text NOT NULL REFERENCES companies(id),
    employee_id         text NOT NULL REFERENCES employees(id),
    conversation_id     text REFERENCES conversations(id),
    roster_id           bigint NOT NULL REFERENCES roster(id),
    change_type         text NOT NULL,
    from_trip_id        text NOT NULL REFERENCES trips(id),
    to_trip_id          text REFERENCES trips(id),
    status              text NOT NULL CHECK (status IN (
                            'APPLIED', 'PENDING_APPROVAL', 'APPROVED_APPLIED', 'MANAGER_REJECTED',
                            'APPROVED_NO_SEAT', 'EXPIRED', 'WITHDRAWN')),
    reason_code         text,
    reason_text         text,
    dedupe_key          text NOT NULL,
    approval_thread_id  text,
    created_at          timestamptz NOT NULL,
    decided_by          text,
    decided_at          timestamptz,
    note                text
);
-- Allowed change types (kept as a named constraint so existing databases pick up new types).
ALTER TABLE shift_change_requests DROP CONSTRAINT IF EXISTS shift_change_requests_change_type_check;
ALTER TABLE shift_change_requests ADD CONSTRAINT shift_change_requests_change_type_check
    CHECK (change_type IN ('reschedule', 'cancel', 'restore'));

-- At most one pending request for the same change: double-submits collapse.
CREATE UNIQUE INDEX IF NOT EXISTS scr_one_pending
    ON shift_change_requests (company_id, dedupe_key) WHERE status = 'PENDING_APPROVAL';

CREATE TABLE IF NOT EXISTS audit_log (
    id          bigserial PRIMARY KEY,
    company_id  text NOT NULL REFERENCES companies(id),
    actor_type  text NOT NULL,
    actor_id    text NOT NULL,
    action      text NOT NULL,
    request_id  text,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_by_company ON audit_log (company_id, id DESC);
