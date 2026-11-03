-- Projects are removed. The portal lists each employee's applications directly, so the grouping is no longer needed.
ALTER TABLE applications DROP COLUMN IF EXISTS project_id;
DROP TABLE IF EXISTS projects;
