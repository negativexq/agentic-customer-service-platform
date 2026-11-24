# Operator Console Frontend

Before changing this frontend, read:

- [`../skills/ui-ux-pro-max/SKILL.md`](../skills/ui-ux-pro-max/SKILL.md) for the project-local design workflow.

The console uses React, TypeScript, Vite, Tailwind CSS, and npm. Keep frontend refinements within this stack unless a dependency is clearly necessary and explicitly approved. Do not change backend contracts as part of a UX refinement.

Install dependencies reproducibly with `npm ci`. Before submitting a change, run
`npm run typecheck`, `npm run lint`, `npm test`, and `npm run build`.

The production nginx image runs unprivileged on port 8080. It applies security headers, serves
fingerprinted `/assets/` files with immutable caching, keeps HTML and proxied API responses
revalidatable or non-cacheable, and routes unknown client paths through the SPA entry point.

## Local authenticated development

Create the repository-root environment file first, then start the backend and Vite:

```bash
cp .env.example .env
uv sync --frozen
make migrate
make seed
make dev
```

```bash
cd frontend
npm ci
npm run dev
```

Vite reads `FRONTEND_AUTH_MODE` from the root `.env` and creates one explicit in-memory auth
provider. In `local_demo` mode it reads the intentionally non-secret `LOCAL_DEMO_AUTH_TOKEN`, and
in `integration` mode Compose supplies the deterministic CI credential. The API client centrally
attaches the Bearer header and does not write credentials to localStorage or include them in
inspector projections. Set
`VITE_BACKEND_TARGET` to override the default `http://localhost:8000` proxy destination. The proxy
covers `/agent`, `/ui`, `/customers`, `/orders`, `/tickets`, `/memories`, `/escalations`, `/health`,
and `/ready` without changing frontend API contracts.

For the container workflow, run `cp .env.example .env` and `docker compose up --build` from the
repository root, then open <http://localhost:5173>. nginx proxies the same API routes to the backend
and explicitly forwards `Authorization` while retaining the existing CSP and security headers.

The Operator Console memory panel receives lifecycle metadata only. The `/ui/memory` client type
does not include persisted memory body text, and the panel displays type, normalized key, source,
status, timestamps, and expiration. Memory content remains available internally to the
agent runtime according to memory policy; the console has no reveal path.

`local-demo-support-token` is deterministic, public localhost/demo configuration. It must never be
used as a production credential. The production Compose overlay builds with
`FRONTEND_AUTH_MODE=external_session` and no demo token. Production therefore shows a fail-closed
authentication state until a trusted external session/BFF integration supplies the documented
`window.__OPERATOR_AUTH__` adapter. That adapter may use an HTTP-only session cookie or an
externally acquired access credential; the repository does not ship an enterprise login flow.

The frontend auth provider has four states: loading, authenticated, unauthenticated, and
misconfigured. It gates protected API calls, distinguishes 401 from 403, clears in-memory operator
data after a 401, and never persists credentials. The external adapter is the integration boundary
for a future OIDC/OAuth2 PKCE flow, BFF, auth gateway, or reverse-proxy identity layer.

The console keeps authentication, API reachability, and backend runtime readiness separate. A
successful authenticated `/ui/system-health` response is rendered as `ready` or `not ready` with
component states such as `healthy`, `unavailable`, `incompatible`, `configured`, and `not_probed`.
The console does not display a static “API connected” claim, and it does not label a configured LLM
healthy because no active provider probe is performed.
