# Production host port policy

Render production with `docker compose -f docker-compose.yml -f docker-compose.prod.yml config`.
The production override resets inherited port publications for PostgreSQL, Qdrant,
Jaeger, the backend, and the optional metrics Collector. These services remain
reachable by service name on their Compose networks; host publication is not
needed for application traffic.

The frontend is the sole public HTTP ingress (container TCP/8080, host
`FRONTEND_PORT`, default 5173). An external TLS ingress can forward traffic to
this endpoint. The optional Prometheus inspection endpoint remains explicitly
loopback-only (`127.0.0.1:PROMETHEUS_PORT`, default 9090). The Collector's OTLP
receiver is not published. Development and integration Compose retain their own
host publications for local tooling and smoke tests.

Run `python scripts/validate_production_topology.py` before deployment. It
renders the metrics profile too, rejects any host port on internal services,
rejects arbitrary frontend ports, and rejects non-loopback Prometheus access.
The check validates the Compose model, not the deployed host's firewall or
external ingress configuration.
