-- Marketing Mailer (repository Marketing-app): one application, its FastAPI backend and React frontend served on one
-- address. Like the others it starts disabled with no launch URL; a super administrator sets the URL and enables it.
-- `external_role` is the value the marketing backend enforces (require_role("admin", "member") in its routes).

INSERT INTO applications (slug, name, description, integration_type) VALUES
    ('marketing', 'Marketing Mailer',
     'Marketing email to media contacts and partners, and information requests to staff and partners.', 'none')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO app_roles (application_id, name, external_role, description)
SELECT a.id, r.name, r.external_role, r.description
FROM applications a
CROSS JOIN (VALUES
    ('admin', 'admin', 'Manages contacts and imports, sends test and live campaigns, invites media contacts.'),
    ('member', 'member', 'Views contacts, lists and templates, and drafts campaigns.')
) AS r (name, external_role, description)
WHERE a.slug = 'marketing'
ON CONFLICT (application_id, name) DO NOTHING;
