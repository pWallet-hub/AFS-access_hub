-- Procurement: the department head's title is "Manager of Department" (was "Director of Department").
-- Only the description shown to administrators changes. The role name and the external_role sent to Procurement stay
-- `director_dept`, so existing assignments keep working.
UPDATE app_roles r SET description = 'Manager of Department.'
FROM applications a
WHERE a.id = r.application_id AND a.slug = 'procurement' AND r.name = 'director_dept';
