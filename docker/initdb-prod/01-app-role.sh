#!/bin/sh
# First boot of the production database: create the non-superuser application role.
# Password comes from APP_DB_PASSWORD in .env.production; DATABASE_URL must use quantum_app.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOF
CREATE ROLE quantum_app LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
ALTER DATABASE ${POSTGRES_DB} OWNER TO quantum_app;
GRANT ALL ON SCHEMA public TO quantum_app;
EOF
