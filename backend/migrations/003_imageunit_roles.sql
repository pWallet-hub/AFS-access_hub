-- ImageUnit's application-specific roles. `external_role` is the exact role value ImageUnit stores on
-- its own users (its users.role enum), so the mapping from a central assignment is explicit.

INSERT INTO app_roles (application_id, name, external_role, description)
SELECT a.id, r.name, r.external_role, r.description
FROM applications a
CROSS JOIN (VALUES
    ('admin', 'admin', 'Full administration of ImageUnit, including users and settings.'),
    ('editor', 'editor', 'Manages events, galleries and the photo library.'),
    ('photographer', 'photographer', 'Uploads and edits photos for events they are invited to.')
) AS r (name, external_role, description)
WHERE a.slug = 'imageunit'
ON CONFLICT (application_id, name) DO NOTHING;
