#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_role DB_USER
\getenv app_password DB_PASSWORD
SELECT format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'app_role', :'app_password')
\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', current_database(), :'app_role')
\gexec
SQL
