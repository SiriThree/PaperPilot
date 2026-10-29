-- Shared across application workers; keyed by a digest of the ASGI peer address.
CREATE TABLE auth_login_limits (
    peer_hash bytea PRIMARY KEY CHECK (octet_length(peer_hash) = 32),
    window_start timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL CHECK (attempts > 0)
);
CREATE INDEX auth_login_limits_window ON auth_login_limits(window_start);
