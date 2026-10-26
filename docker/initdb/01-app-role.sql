-- Runs once when the Postgres volume is first created (docker-entrypoint-initdb.d).
-- The application must NOT connect as a superuser: superusers bypass row-level security.
-- quantum_app owns the database (so migrations can create tables and policies) but has
-- neither SUPERUSER nor BYPASSRLS. CREATEDB is only for the test database.
CREATE ROLE quantum_app LOGIN PASSWORD 'quantum' NOSUPERUSER NOBYPASSRLS CREATEDB;
ALTER DATABASE quantumloop OWNER TO quantum_app;
GRANT ALL ON SCHEMA public TO quantum_app;
