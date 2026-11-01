-- Explicit lifecycle compatibility for B credential logins; no account data is changed.
ALTER TABLE paperpilot_security.service_logins DROP CONSTRAINT service_logins_kind_check;
ALTER TABLE paperpilot_security.service_logins ADD CONSTRAINT service_logins_kind_check
    CHECK(kind IN ('reader','audit','business','auth'));
