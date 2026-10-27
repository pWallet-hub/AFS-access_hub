-- Application-specific roles for Biocap and Procurement. `external_role` is the role value each application's
-- backend stores and enforces (taken from its source code). Applications stay disabled until a launch URL is set.

INSERT INTO app_roles (application_id, name, external_role, description)
SELECT a.id, r.name, r.external_role, r.description
FROM applications a
CROSS JOIN (VALUES
    ('admin', 'admin', 'Administration of the Biocap site, users and settings.'),
    ('communication_officer', 'communication_officer', 'Manages users, reviews profiles, approves and publishes blogs.'),
    ('editor', 'editor', 'Reviews and publishes blogs.'),
    ('social_media_content_creator', 'social_media_content_creator', 'Creates and submits blogs and social media content.'),
    ('media_correspondent', 'media_correspondent', 'Creates and submits blogs.'),
    ('principal_investigator', 'principal_investigator', 'Principal investigator; creates and submits content.')
) AS r (name, external_role, description)
WHERE a.slug = 'biocap'
ON CONFLICT (application_id, name) DO NOTHING;

INSERT INTO app_roles (application_id, name, external_role, description)
SELECT a.id, r.name, r.external_role, r.description
FROM applications a
CROSS JOIN (VALUES
    ('requesting_staff', 'requesting_staff', 'Raises procurement cases and signs as requester.'),
    ('accountant', 'accountant', 'Accountant / procurement focal person.'),
    ('director_comms', 'director_comms', 'Director of Communications.'),
    ('director_dept', 'director_dept', 'Director of Department.'),
    ('pi', 'pi', 'Principal Investigator.'),
    ('cfm', 'cfm', 'Chief Finance Manager.'),
    ('market_verifier', 'market_verifier', 'Market verification officer.'),
    ('superior', 'superior', 'Superior staff (IM-08 decision).'),
    ('admin', 'admin', 'System administrator.')
) AS r (name, external_role, description)
WHERE a.slug = 'procurement'
ON CONFLICT (application_id, name) DO NOTHING;
