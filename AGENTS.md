# Repository Guidelines

## Project Layout

RongHub is the Fuzhou charging supervision platform. Read `README.md` for current paths and `项目上下文.md` for the project handoff. Initial applications are implemented; consult `docs/project/工作地图.md` and `docs/project/本地开发.md` for verified progress and startup. Do not claim unimplemented workflows are complete.

- `apps/server/`: FastAPI API and worker code, tests and Alembic migrations.
- `apps/web/`: Next.js and TypeScript application for three Web roles.
- `packages/api-client/`: generated OpenAPI TypeScript declarations; typed request adoption is in progress.
- `tests/e2e/`, `deploy/`, `scripts/`: system verification, deployment and tooling.
- `docs/`: requirements, architecture, design, project notes and research.
- `references/customer/`: original workbook and intact Mockplus prototype.
- `references/standards/`: protocol references, subject to applicability checks.
- `outputs/`: deliverables; preserve quotation versions.

## Implementation Direction

Use Python/FastAPI, Next.js and PostgreSQL. The management mini program is deferred. Treat customer prototypes as the business baseline while improving visual quality, usability and architecture. Confirm substantive business changes and trace them to requirements.

## Development and Verification

Python uv.lock and frontend pnpm-lock.yaml exist. Run documented commands from the correct application directories. Preserve generated prototype files and Chinese reference filenames. Inspect hidden workbook sheets when assessing requirements.

After API model changes, run `pnpm api:generate`, `pnpm api:check` and `pnpm typecheck` in `apps/web`; never hand-edit generated declarations. Unknown response schemas are incomplete contracts, not permission to cast arbitrary JSON.

Verify permissions, cross-operator isolation, approval transitions, Decimal calculations, synchronization idempotency and recovery. Use real PostgreSQL for database behavior tests. Check representative UI against the prototype before expanding the design.

## Configuration and Handoff

Do not commit credentials, tokens or real customer records. Public portal publication requires explicit consent and `content.public`; authenticated `audience=all` never implies anonymous access. Keep sanitized configuration examples and dependency lockfiles. Update `项目上下文.md` after material decisions, keeping confirmed facts separate from proposals and preserving known limitations.
