-- ============================================================================
-- Ai-IND-money — Trading audit trail: signal decisions, order lifecycle events,
-- model / regime-map lifecycle events
-- ============================================================================
-- Applied by: scripts/db/migrate.py (GitHub Actions, on deploy). Safe to re-run.
-- Mirrors of the host's journals (skopaq/strategy/journal.py, order journal), written
-- best-effort by the trading processes with the service_role key
-- (skopaq/db/mirror.py); the local journals stay authoritative for trading decisions.
-- RLS on, no policies: browsers (anon / authenticated keys) cannot read them.

CREATE TABLE IF NOT EXISTS signal_events (
    id BIGSERIAL PRIMARY KEY,
    signal_id TEXT NOT NULL,
    event TEXT NOT NULL,                      -- decision | execution | outcome
    mode TEXT,                                -- paper | live
    strategy_id TEXT,
    symbol TEXT,
    status TEXT,                              -- QUALIFIED | REJECTED | EXPIRED | FILLED …
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (signal_id, event)
);

CREATE INDEX IF NOT EXISTS idx_signal_events_created ON signal_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_symbol ON signal_events(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS order_events (
    id BIGSERIAL PRIMARY KEY,
    internal_id TEXT,
    order_id TEXT,
    event TEXT NOT NULL,                      -- submitting | placed | final | …
    lifecycle_state TEXT,                     -- skopaq/execution/order_lifecycle.py
    symbol TEXT,
    side TEXT,
    qty NUMERIC,
    filled NUMERIC,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_order_events_internal ON order_events(internal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id, created_at);

CREATE TABLE IF NOT EXISTS model_events (
    id BIGSERIAL PRIMARY KEY,
    version TEXT,
    event TEXT NOT NULL,                      -- registered | shadow | promoted | rollback …
    by_user TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE signal_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_events ENABLE ROW LEVEL SECURITY;
