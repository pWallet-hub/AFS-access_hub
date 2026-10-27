-- Integration health: when each application last checked access, and the failures it reported.
-- Access checks update the timestamps; applications report sync and revocation failures through
-- /api/internal/events, and token rejections are recorded here too.

ALTER TABLE applications
    ADD COLUMN last_access_check_at timestamptz,
    ADD COLUMN last_access_ok_at timestamptz,
    ADD COLUMN last_error_at timestamptz,
    ADD COLUMN last_error text;

CREATE TABLE integration_events (
    id              bigserial PRIMARY KEY,
    application_id  uuid NOT NULL REFERENCES applications (id),
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    kind            text NOT NULL CHECK (kind IN ('sync_failure', 'revocation_failure', 'token_rejected', 'info')),
    outcome         text NOT NULL CHECK (outcome IN ('success', 'failure')),
    detail          text NOT NULL DEFAULT '' CHECK (length(detail) <= 1000)
);

CREATE INDEX integration_events_app_idx ON integration_events (application_id, occurred_at DESC);
