# Keycloak built for production: the database vendor is fixed at build time, so the server starts in optimized mode.
# Used by docker-compose.prod.yml. Development keeps the stock image and start-dev (docker-compose.yml).
FROM quay.io/keycloak/keycloak:26.0
# Health endpoints are a build-time option in optimized mode, so they are enabled here, not at runtime.
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
