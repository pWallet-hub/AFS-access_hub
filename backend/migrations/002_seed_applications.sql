-- The five applications in the initial integration scope (Task.md section 3).
-- They start disabled with no launch URL. A super administrator sets the verified URL
-- and enables each application once it has been checked. Backend-only repositories are
-- registered for completeness and stay disabled, since they have no launch page.

INSERT INTO applications (slug, name, description, integration_type) VALUES
    ('biocap-rwanda', 'Biocap Rwanda',
     'Biocap Rwanda application. Keeps its existing login and gains AFS SSO.', 'none'),
    ('biocap-dashboard', 'Biocap Dashboard Backend',
     'Backend API for the Biocap dashboard. API-only; no launch page.', 'none'),
    ('procurement-bn', 'Procurement BN',
     'Procurement application (BN). Keeps its existing login and gains AFS SSO.', 'none'),
    ('procurement-fn', 'Procurement FN',
     'Procurement application (FN). Keeps its existing login and gains AFS SSO.', 'none'),
    ('imageunit', 'ImageUnit',
     'ImageUnit application. Keeps its existing login and gains AFS SSO.', 'none')
ON CONFLICT (slug) DO NOTHING;
