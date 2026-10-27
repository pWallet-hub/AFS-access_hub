-- The applications in the initial integration scope (Task.md section 3), one per product. A product with a
-- frontend and a backend is a single application: Biocap (Biocap Rwanda frontend and Biocap Dashboard backend)
-- and Procurement (Procurement FN frontend and Procurement BN backend). ImageUnit is one repository with its web
-- app and API. Applications start disabled with no launch URL. A super administrator sets the verified URL and
-- enables each application once it has been checked.

INSERT INTO applications (slug, name, description, integration_type) VALUES
    ('imageunit', 'ImageUnit',
     'Photo and video management for events. Keeps its existing login and gains AFS SSO.', 'none'),
    ('biocap', 'Biocap',
     'Biocap Rwanda website and dashboard: the Biocap Rwanda frontend and the Biocap Dashboard backend.', 'none'),
    ('procurement', 'Procurement',
     'Procurement and e-signature system: the Procurement FN portal and the Procurement BN backend.', 'none')
ON CONFLICT (slug) DO NOTHING;
