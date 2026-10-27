-- Command Center core schema.
-- Users are identified by Keycloak's stable `sub` claim. Rows created by an administrator
-- before first login have keycloak_sub NULL and are linked on the user's first verified sign-in.

CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    keycloak_sub    text UNIQUE,
    email           text NOT NULL,
    display_name    text,
    email_verified  boolean NOT NULL DEFAULT false,
    platform_role   text NOT NULL DEFAULT 'employee'
                    CHECK (platform_role IN ('super_admin', 'access_admin', 'employee')),
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    last_login_at   timestamptz
);

-- Pre-provisioned (not yet linked) accounts must be unique by email.
CREATE UNIQUE INDEX users_pending_email_key ON users (lower(email)) WHERE keycloak_sub IS NULL;

CREATE TABLE applications (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug             text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
    name             text NOT NULL,
    description      text NOT NULL DEFAULT '',
    launch_url       text,
    enabled          boolean NOT NULL DEFAULT false,
    integration_type text NOT NULL DEFAULT 'none' CHECK (integration_type IN ('none', 'oidc')),
    oidc_client_id   text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    -- An application cannot be enabled for launching without a launch URL.
    CHECK (NOT enabled OR launch_url IS NOT NULL)
);

-- Application-specific roles. `external_role` is the role name the destination application
-- itself recognises, so the mapping from a central role to the app's privileges is explicit.
CREATE TABLE app_roles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  uuid NOT NULL REFERENCES applications (id),
    name            text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
    external_role   text NOT NULL CHECK (length(external_role) BETWEEN 1 AND 120),
    description     text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (application_id, name),
    UNIQUE (id, application_id)
);

-- Assignments grant an employee access to one application. Revoking sets revoked_at, which keeps
-- history. A role must belong to the same application (enforced by the composite foreign key).
CREATE TABLE assignments (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users (id),
    application_id  uuid NOT NULL REFERENCES applications (id),
    app_role_id     uuid,
    granted_by      uuid REFERENCES users (id),
    granted_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz,
    revoked_by      uuid REFERENCES users (id),
    FOREIGN KEY (app_role_id, application_id) REFERENCES app_roles (id, application_id)
);

CREATE UNIQUE INDEX assignments_one_active ON assignments (user_id, application_id) WHERE revoked_at IS NULL;

-- Append-only audit trail. Actor and target are stored as plain values, not foreign keys,
-- so the record stays readable even if a related row changes later.
CREATE TABLE audit_log (
    id               bigserial PRIMARY KEY,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    actor_user_id    uuid,
    actor_identity   text NOT NULL,
    action           text NOT NULL,
    target_type      text NOT NULL,
    target_id        text,
    application_slug text,
    outcome          text NOT NULL CHECK (outcome IN ('success', 'failure')),
    details          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_occurred_idx ON audit_log (occurred_at DESC);

CREATE OR REPLACE FUNCTION forbid_audit_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_change();

CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_audit_change();
