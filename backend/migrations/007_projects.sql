-- Projects group applications. A project is a product (for example ImageUnit, or Procurement with its portal and
-- backend). An application may belong to one project; applications created before projects existed stay unassigned
-- until an administrator sets their project.

CREATE TABLE projects (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
    name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    description  text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now()
);

INSERT INTO projects (slug, name, description) VALUES
    ('imageunit', 'ImageUnit', 'Photo and video management for events.'),
    ('biocap', 'Biocap', 'Biocap Rwanda website and dashboard.'),
    ('procurement', 'Procurement', 'Procurement and e-signature system.'),
    ('demo', 'Demo', 'Demonstration applications.')
ON CONFLICT (slug) DO NOTHING;

ALTER TABLE applications ADD COLUMN project_id uuid REFERENCES projects (id);

UPDATE applications SET project_id = p.id FROM projects p
WHERE p.slug = applications.slug;

CREATE INDEX applications_project_idx ON applications (project_id);
