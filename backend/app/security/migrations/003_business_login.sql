-- Explicit upgrade of the lifecycle registry; does not change any existing role's grants.
ALTER TABLE paperpilot_security.service_logins DROP CONSTRAINT service_logins_kind_check;
ALTER TABLE paperpilot_security.service_logins ADD CONSTRAINT service_logins_kind_check
    CHECK (kind IN ('reader', 'audit', 'business'));
