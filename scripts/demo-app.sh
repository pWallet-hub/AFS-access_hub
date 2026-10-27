#!/usr/bin/env bash
# Add or remove a demo application for demonstrations. It is not part of the migrations, so it never appears in
# production unless someone runs this script.
#
#   scripts/demo-app.sh add [employee-email]   register the demo app (enabled), with two roles; optionally assign
#                                              the given employee as "editor"
#   scripts/demo-app.sh remove                 delete the demo app, its roles and its assignments
set -euo pipefail

cd "$(dirname "$0")/.."
action="${1:-}"
employee="${2:-}"
psql_cmd() { docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d afs_command_center' ; }

case "$action" in
  add)
    {
      cat <<'SQL'
INSERT INTO applications (slug, name, description, launch_url, enabled, integration_type, project_id)
VALUES ('demo', 'Demo App', 'A placeholder application for demonstrations. It shows the launcher working end to end.',
        'http://localhost:8080/demo/', true, 'none', (SELECT id FROM projects WHERE slug = 'demo'))
ON CONFLICT (slug) DO UPDATE SET launch_url = EXCLUDED.launch_url, enabled = true, project_id = EXCLUDED.project_id, updated_at = now();

INSERT INTO app_roles (application_id, name, external_role, description)
SELECT a.id, r.name, r.name, r.description FROM applications a
CROSS JOIN (VALUES
  ('viewer', 'Can view demo content.'),
  ('editor', 'Can change demo content.')
) AS r (name, description)
WHERE a.slug = 'demo'
ON CONFLICT (application_id, name) DO NOTHING;
SQL
      if [ -n "$employee" ]; then
        cat <<SQL
INSERT INTO assignments (user_id, application_id, app_role_id)
SELECT u.id, a.id, r.id FROM users u, applications a, app_roles r
WHERE lower(u.email) = lower('$employee') AND a.slug = 'demo' AND r.application_id = a.id AND r.name = 'editor'
  AND NOT EXISTS (SELECT 1 FROM assignments s WHERE s.user_id = u.id AND s.application_id = a.id AND s.revoked_at IS NULL);
SQL
      fi
    } | psql_cmd
    echo "demo app is registered and enabled: http://localhost:8080/demo/"
    if [ -n "$employee" ]; then
      # An account exists only after its first sign-in, so the assignment is skipped until then.
      if [ -n "$(docker compose exec -T db sh -c "psql -tA -U \"\$POSTGRES_USER\" -d afs_command_center -c \"SELECT 1 FROM assignments s JOIN users u ON u.id = s.user_id JOIN applications a ON a.id = s.application_id WHERE a.slug = 'demo' AND s.revoked_at IS NULL AND lower(u.email) = lower('$employee')\"")" ]; then
        echo "assigned $employee as editor"
      else
        echo "no assignment made: $employee has not signed in to Command Center yet. Sign in once, then run this again." >&2
      fi
    fi
    ;;
  remove)
    cat <<'SQL' | psql_cmd
DELETE FROM assignments WHERE application_id IN (SELECT id FROM applications WHERE slug = 'demo');
DELETE FROM app_roles WHERE application_id IN (SELECT id FROM applications WHERE slug = 'demo');
DELETE FROM applications WHERE slug = 'demo';
SQL
    echo "demo app removed"
    ;;
  *)
    echo "usage: scripts/demo-app.sh add [employee-email] | remove" >&2
    exit 2
    ;;
esac
