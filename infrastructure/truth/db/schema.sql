-- Truth Layer schema (Single Source of Truth)
-- Append-only event sourcing for the ARGOS trading system.
--
-- Invariants:
--   1. Append-only: NEVER UPDATE, NEVER DELETE, NEVER OVERWRITE
--   2. Deterministic replay: replay(events[0:n]) == original state
--   3. Event purity: event(payload) = f(system_state_t, input_t)
--   4. Hash chaining: state_hash_t = SHA256(payload + parent_hash + event_type + timestamp)

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    run_id      TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,  -- TRADE | EPISODE | TENSOR | INFERENCE | CONTROL

    payload     TEXT    NOT NULL,  -- JSON-serialized event data

    state_hash  TEXT    NOT NULL,  -- SHA256 integrity hash
    parent_hash TEXT    NOT NULL,  -- previous event's state_hash ('' for first event)

    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_events_run_symbol_time
    ON events(run_id, symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_events_event_type
    ON events(event_type);

CREATE INDEX IF NOT EXISTS idx_events_created_at
    ON events(created_at);
