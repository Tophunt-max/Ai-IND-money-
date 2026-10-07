-- ============================================================================
-- Ai-IND-money — Web dashboard login history
-- ============================================================================
-- Applied via: supabase db push  (or Supabase dashboard SQL editor)
-- One row per dashboard session (status 'ok') and per refused account ('denied'),
-- written by the API (skopaq/api/dashboard_auth.py) with the service_role key.
-- RLS on, no policies: browsers (anon / authenticated keys) cannot read it.

CREATE TABLE IF NOT EXISTS dashboard_logins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',          -- ok | denied
    user_id UUID,
    email TEXT NOT NULL,
    role TEXT,                                  -- admin | viewer (NULL when denied)
    provider TEXT,                              -- email | google
    ip TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, status)
);

CREATE INDEX IF NOT EXISTS idx_dashboard_logins_email ON dashboard_logins(email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dashboard_logins_created ON dashboard_logins(created_at DESC);

ALTER TABLE dashboard_logins ENABLE ROW LEVEL SECURITY;
