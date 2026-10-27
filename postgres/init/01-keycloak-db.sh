#!/bin/sh
# Runs once, when the Postgres volume is first initialised.
# Keycloak gets its own database so its schema never mixes with Command Center's.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE keycloak;
EOSQL
