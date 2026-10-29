-- Explicit administrator install; application startup never creates credentials or accounts.
CREATE SCHEMA paperpilot_auth;
REVOKE ALL ON SCHEMA paperpilot_auth FROM PUBLIC;
CREATE TABLE paperpilot_auth.schema_version(version integer PRIMARY KEY CHECK (version=1));
INSERT INTO paperpilot_auth.schema_version VALUES(1);
CREATE TABLE paperpilot_auth.accounts (
    user_id uuid PRIMARY KEY REFERENCES public.users(user_id),
    password_hash text NOT NULL CHECK(length(password_hash)<=256),
    credential_version uuid NOT NULL,
    disabled boolean NOT NULL DEFAULT false,
    last_authenticated_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE paperpilot_auth.sessions (
    token_hash char(64) PRIMARY KEY CHECK(token_hash ~ '^[a-f0-9]{64}$'),
    user_id uuid NOT NULL REFERENCES paperpilot_auth.accounts(user_id),
    credential_version uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK(expires_at>created_at)
);
CREATE INDEX auth_sessions_expiry ON paperpilot_auth.sessions(expires_at);
CREATE TABLE paperpilot_auth.throttle (
    key_hash char(64) PRIMARY KEY CHECK(key_hash ~ '^[a-f0-9]{64}$'),
    window_started_at timestamptz NOT NULL,
    attempts integer NOT NULL CHECK(attempts>=1)
);
REVOKE ALL ON ALL TABLES IN SCHEMA paperpilot_auth FROM PUBLIC;
ALTER TABLE paperpilot_security.service_logins DROP CONSTRAINT service_logins_kind_check;
ALTER TABLE paperpilot_security.service_logins ADD CONSTRAINT service_logins_kind_check
    CHECK(kind IN ('reader','audit','business','auth'));
