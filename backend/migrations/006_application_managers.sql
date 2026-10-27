-- Scoped delegation: an access administrator can manage access (assignments and role mappings) only for the
-- applications listed here. Super administrators are not limited by this table.

CREATE TABLE application_managers (
    user_id         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    application_id  uuid NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    granted_by      uuid REFERENCES users (id),
    granted_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, application_id)
);
