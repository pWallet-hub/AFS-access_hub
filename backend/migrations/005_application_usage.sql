-- Sessions per application per day. A row is counted each time Command Center grants an application access
-- (sign-in, or a token refresh that asks again). Counts only: no identities are stored here.
CREATE TABLE application_usage (
  application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  day date NOT NULL,
  sessions integer NOT NULL DEFAULT 0,
  PRIMARY KEY (application_id, day)
);
