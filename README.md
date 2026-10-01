# Agentic Customer Service Platform

**A production-oriented Agentic AI Control Plane for reliable customer-support workflows.**

This reference implementation answers knowledge questions and performs controlled actions such as order lookup, refunds, cancellations, ticket creation, and human escalation.

Unlike a typical tool-calling chatbot, the LLM never receives direct mutation authority. It produces a semantic proposal; authentication, scope, target resolution, policy, confirmation, revalidation, idempotency, and execution remain deterministic and server-owned.

> **The LLM proposes. Deterministic software decides what may execute.**

![Refund workflow held at a confirmation boundary](docs/demo/refund-happy-path.png)

## What the platform does

### Customer support

- Look up orders and tickets.
- Request refunds and cancel orders through controlled workflows.
- Create support tickets and escalate to a human specialist.

### Knowledge

- Answer policy and FAQ questions with hybrid retrieval and grounded evidence.
- Validate excerpts and citations, then abstain when evidence is insufficient.
- Keep retrieved knowledge separate from execution authority.

### Stateful workflows

- Hold consequential actions at explicit confirmation boundaries.
- Suspend, resume, replace, and recover workflows across browser or backend
  restarts.
- Revalidate business state and exact action arguments before execution.
- Prevent duplicate business effects with idempotency and database constraints.

## How a request moves through the system

Consider a customer saying:

> “Cancel my latest order.”

```text
Request → auth/context → LLM proposal
       → target resolution → business validation → policy
       → confirmation (if required) → revalidation
       → typed tool → idempotency + DB → operator audit
```

For the proposal, `intent = cancel_order` and `target = latest_order`. The
model does not choose a trusted order ID or execute the cancellation. The
backend resolves that reference within the authenticated customer scope, then
checks ownership and cancellability before policy and confirmation.

## What the model controls — and what it doesn't

| Model / probabilistic | Deterministic / server-owned |
| --- | --- |
| Interpret natural-language requests | Authentication and request context |
| Propose semantic intent | Tenant and customer scope |
| Interpret ambiguous language | Business target resolution |
| Help generate customer responses | Typed tool arguments |
| Use retrieved context for answers | Risk and policy decisions |
| Suggest an action | Confirmation state |
|  | Post-confirmation revalidation |
|  | Idempotency and replay handling |
|  | Database ownership/state constraints |
|  | Execution authority |

The model is an untrusted semantic component, not an authorization or transaction engine. Memory enriches context; RAG supports answers; neither can grant authority.

## Why not let the LLM call tools directly?

A direct design looks like:

```text
LLM → refund_order(order_id=123) → execute
```

This platform keeps the authority boundary explicit:

```text
LLM → semantic proposal
    → target resolution → authorization/validation
    → policy → confirmation (if required) → revalidation
    → typed tool → idempotency + database
```

A model-generated identifier or tool argument is never trusted merely because
the model produced it.

## Architecture

~~~mermaid
flowchart TB
    AUTH[Authenticated Request] --> SECURITY[Security Boundary]
    SECURITY --> PROPOSAL[LLM Semantic Proposal]
    PROPOSAL --> COMPILER[Deterministic Compiler]
    COMPILER --> TARGET[Business Target Resolution]
    TARGET --> VALIDATE[Typed / Business Validation]
    VALIDATE --> POLICY[Policy]
    POLICY --> DECISION{Decision}
    DECISION -->|allow| TOOL[Typed Tool]
    DECISION -->|require confirmation| CONFIRM[Confirmation Boundary]
    DECISION -->|human| HUMAN[Human Escalation]
    DECISION -->|deny| BLOCK[No Execution]
    CONFIRM --> REVALIDATE[Revalidation]
    REVALIDATE --> TOOL
    HUMAN --> IDEMPOTENCY[Idempotency + DB]
    TOOL --> IDEMPOTENCY
    IDEMPOTENCY --> TRACE[Operator Projection / Audit]
    BLOCK --> TRACE

    QUESTION[Knowledge Question] --> RETRIEVE[Hybrid Retrieval]
    RETRIEVE --> GROUND[Grounding Validation]
    GROUND --> ANSWER[Bounded Answer]
    MEMORY[Memory = context only] -.-> PROPOSAL
    MEMORY -.-> RETRIEVE
    GROUND -.->|does not grant authority| TRACE
~~~

Security checks precede semantic routing. The compiler combines the proposal with server-owned identity, target, business, policy, confirmation, revalidation, and idempotency state; only typed tools can commit an effect. Knowledge uses hybrid dense/BM25 retrieval and grounding validation, and its evidence never authorizes a mutation.

## Engineering decisions

| Decision | Why |
| --- | --- |
| Semantic proposals instead of trusted tool calls | Model output must not become execution authority. |
| Server-owned target resolution | Prevent fabricated or cross-customer identifiers. |
| Bound confirmation for consequential actions | Keep a human in control of customer-impacting effects. |
| Revalidation before execution | Protect against stale state and TOCTOU changes. |
| Idempotent writes | Prevent duplicate business effects and replay mutations. |
| Unknown write outcomes are not blindly retried | Avoid double mutations after ambiguous network failure. |
| Memory is context-only | Stored text cannot create permissions. |
| RAG evidence cannot grant authority | Knowledge and execution remain separate. |
| Database constraints enforce critical invariants | Application checks are not the last line of defense. |

## Showcase scenarios

These are deterministic projections from the existing operator console, not live production telemetry or certification.

### Controlled mutation: refund confirmation

A refund proposal is held behind confirmation; its exact pending payload is revalidated before the typed tool runs.

### Security boundary: instruction-override denial

An instruction-override attempt is rejected before it becomes an authorized workflow; the projection separates deny, authority, and execution state.

![Security boundary containing an instruction override](docs/demo/security-boundary.png)

### Grounded intelligence: RAG evidence

A policy answer is shown beside retrieved sources and grounding status. Evidence supports response generation; it does not grant authority.

![RAG grounded response and evidence](docs/demo/rag-grounded-faq-conversation.png)

More scenarios are in the [demo walkthrough](docs/demo/walkthrough.md).

## Evaluation and release evidence

Semantic, operational, resilience, and real-LLM evidence remain separate; denominators are preserved and not merged into a synthetic score.

| Evidence slice | Recorded result |
| --- | --- |
| D2c semantic/safety validation | 540/540 measured attempts; 0 unsafe executable survivors; 0 unsafe executions |
| D2d operational release gate | 18/18 operational scenarios; 8/8 mandatory phases; 6/6 fault classes |
| Deterministic resilience snapshot | 28 scenarios, 28 passed; run `eval-9fc295817532` |
| Real-LLM adversarial audit | 100 real-LLM samples: 82 passed all evaluated assertions; 18 produced bounded semantic/quality warnings or partial outcomes. No safety invariant failed; there were 0 unauthorized mutations, confirmation bypasses, duplicate effects, authority-bearing memory writes, or customer-data disclosures. |
| Latest automated release checks | Backend 844 passed; frontend 49 passed; Playwright 6/6; authenticated smoke passed |

The 82/18 split is a quality-outcome breakdown, not a safety rate.

The current release evidence is **READY WITH WARNINGS**: remaining warnings are bounded observability or unverified coverage, not observed safety failures. This is reference-deployment evidence, not unrestricted production certification.

See the [evaluation overview](docs/evaluation-overview.md), [release evidence](docs/release-evidence.md), [final release QA report](docs/security/final-release-qa-report.md), and [real-LLM QA report](docs/security/real-llm-production-qa-report.md).

## Operator observability

The operator console is a read-only investigation surface connecting:

~~~text
Request → Evidence → Proposal → Decision → Authority → Outcome
~~~

Operators inspect bounded request/workflow state, memory/RAG evidence, proposals, validation, policy, confirmation, authority, execution, lifecycle, and replay results. The backend projection is authoritative; the console omits chain-of-thought, raw prompts/responses, secrets, and model token streams.

## Technical stack

| Area | Technology |
| --- | --- |
| Agent orchestration | LangGraph + PostgreSQL checkpoints |
| API | FastAPI + Pydantic |
| Persistence | PostgreSQL + SQLAlchemy + Alembic |
| Retrieval | Qdrant + dense/BM25 hybrid retrieval |
| Observability | FastAPI native OpenTelemetry + Jaeger; opt-in Collector/Prometheus metrics |
| Frontend | React + TypeScript + Vite + Tailwind |
| Verification | Pytest + Ruff + Mypy + Vitest + Playwright |
| Runtime | Docker Compose |

## Run locally

Requirements: Docker Compose, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js, and npm.

~~~bash
cp .env.example .env
docker compose up --build --detach
~~~

Open <http://localhost:5173>. Setup applies migrations, seeds records, and loads the bundled knowledge base; stop with `docker compose down`. The optional live path uses an OpenAI-compatible provider for semantic proposals only; grounding, compilation, policy, confirmation, idempotency, and execution authority remain server-owned.

See [deployment](docs/deployment.md) for health, readiness, and topology details.

## Testing

~~~bash
make test && make lint && make typecheck
make frontend-test && make frontend-typecheck && make frontend-lint && make frontend-build
make eval && make eval-safety && make eval-resilience
~~~

For isolated browser journeys:

~~~bash
npm --prefix frontend ci
npx --prefix frontend playwright install chromium
bash scripts/run_operator_e2e.sh
~~~

Journeys cover grounded proposals, prompt-injection containment, replay, clarification, RAG evidence, and run investigation.

## Project structure

~~~text
app/                 FastAPI, LangGraph graph, policy, persistence, RAG, tools
frontend/            React/TypeScript/Vite/Tailwind Operator Console
tests/               Backend unit and integration tests
evaluation/          Deterministic evaluation and release-gate tooling
docs/                Architecture, deployment, evaluation, security, and demos
alembic/             Database migrations
scripts/              Seed, ingestion, validation, and integration helpers
docker-compose*.yml  Local, integration, and production Compose definitions
Makefile             Development and verification commands
~~~

## Limitations and scope

- This is a production-oriented reference implementation, not a managed production service, compliance certification, or claim of real customer-service production traffic.
- Model semantic errors can still occur; deterministic containment does not prove unseen failures are impossible.
- The console shows bounded projections and omits raw prompts/responses, secrets, unrestricted memory, and hidden reasoning.
- Evidence covers the reference deployment; it does not certify public-internet TLS, enterprise IdP provisioning, multi-region operation, regulatory compliance, or unrestricted capacity.
- Live provider configuration, browser login, secret management, and deployment ownership remain environment-specific.

## Deep-dive documentation

- [Architecture](docs/architecture.md)
- [Identity and security boundaries](docs/security.md)
- [Distributed reliability boundaries](docs/reliability.md)
- [Memory privacy boundary](docs/memory-privacy.md)
- [Engineering hardening roadmap](docs/engineering-hardening-roadmap.md)
- [Evaluation overview](docs/evaluation-overview.md)
- [Evaluation artifact retention policy](docs/evaluation-artifact-policy.md)
- [Release evidence](docs/release-evidence.md)
- [Observability architecture and rollback](docs/observability.md)
- [Deployment expectations](docs/deployment.md)
- [Production demo walkthrough](docs/demo/walkthrough.md)
- [Frontend design guidelines](docs/frontend-design-guidelines.md)

## License

MIT — see LICENSE.
