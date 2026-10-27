.PHONY: sync dev up down test lint typecheck migrate seed rag-ingest rag-reset eval eval-safety eval-resilience eval-rag-grounding eval-report eval-baseline d2d-dry-run verify-evidence observability-up observability-down frontend-install frontend-dev frontend-build frontend-test frontend-typecheck frontend-lint ci-backend ci-frontend security-audit docker-validate production-topology-validate production-config-validate recovery-validate capacity-benchmark capacity-db-benchmark capacity-postgres-benchmark capacity-load e2e-smoke

sync:
	uv sync --frozen

dev:
	uv run --frozen uvicorn app.main:app --reload

up:
	docker compose up --build -d

down:
	docker compose down

test:
	uv run --frozen pytest

lint:
	uv run --frozen ruff check . && uv run --frozen ruff format --check .

typecheck:
	uv run --frozen mypy app tests evaluation scripts

migrate:
	uv run --frozen alembic upgrade head

seed:
	uv run --frozen python -m scripts.seed

rag-ingest:
	uv run --frozen python -m scripts.rag_ingest

rag-reset:
	uv run --frozen python -c "from app.core.config import get_settings; from app.rag.embeddings import DeterministicEmbeddingProvider; from app.rag.storage.qdrant import QdrantKnowledgeStore; s=get_settings(); c=QdrantKnowledgeStore(s.qdrant_url, s.qdrant_collection, DeterministicEmbeddingProvider()); c.client.delete_collection(s.qdrant_collection) if c.client.collection_exists(s.qdrant_collection) else None"

eval:
	uv run --frozen python -m evaluation.runner

eval-safety:
	uv run --frozen python -m evaluation.runner --safety

eval-resilience:
	uv run --frozen python -m evaluation.runner --resilience

eval-rag-grounding:
	uv run --frozen python -m evaluation.rag_grounding_audit

eval-report:
	uv run --frozen python -m evaluation.runner

eval-baseline:
	uv run --frozen python -m evaluation.runner --save-baseline

d2d-dry-run:
	uv run --frozen python -m evaluation.d2d.runner

verify-evidence:
	@test -n "$(MANIFEST)" || (echo "Usage: make verify-evidence MANIFEST=... [ROOT=...] [SOURCE_SHA=...] [SCHEMA_VERSION=...]" >&2 && exit 2)
	uv run --frozen python -m app.evidence.cli "$(MANIFEST)" \
		--root "$(or $(ROOT),artifacts/evidence-payloads)" \
		$(if $(SOURCE_SHA),--source-sha "$(SOURCE_SHA)",) \
		$(if $(SCHEMA_VERSION),--schema-version "$(SCHEMA_VERSION)",)

observability-up:
	docker compose up --build -d

observability-down:
	docker compose down

frontend-install:
	cd frontend && npm ci

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-test:
	cd frontend && npm test

frontend-typecheck:
	cd frontend && npm run typecheck

frontend-lint:
	cd frontend && npm run lint

ci-backend: sync lint typecheck test

ci-frontend: frontend-install frontend-typecheck frontend-lint frontend-test frontend-build

security-audit: sync frontend-install
	uv run --frozen pip-audit
	cd frontend && npm audit --audit-level=high

docker-validate:
	docker compose config --quiet
	docker build --tag customer-service-backend:local .
	docker build --tag customer-service-frontend:local frontend

production-topology-validate:
	uv run --frozen python scripts/validate_production_topology.py

production-config-validate:
	uv run --frozen python scripts/validate_production_config.py

recovery-validate:
	@test -n "$(MANIFEST)" || (echo "Usage: make recovery-validate MANIFEST=... [ROOT=...]" >&2 && exit 2)
	uv run --frozen python scripts/validate_recovery.py "$(MANIFEST)" --root "$(or $(ROOT),artifacts/evidence-payloads)"

capacity-benchmark:
	uv run --frozen python scripts/run_capacity_benchmark.py

capacity-db-benchmark:
	uv run --frozen python scripts/benchmark_postgres_capacity.py

capacity-postgres-benchmark:
	uv run --frozen python -m scripts.benchmark_postgres_application

capacity-load:
	uv run --frozen python scripts/load_test_capacity.py

e2e-smoke:
	python3 scripts/e2e_authenticated_smoke.py
