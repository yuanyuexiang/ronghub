# Repository Guidelines

## Project Structure

RongHub is Fuzhou’s charging supervision platform for regulators, associations, and operators. The web app also serves the public portal. Mini program development is deferred.

- `apps/server/app/` contains the FastAPI API and sync worker. Alembic revisions live in `apps/server/alembic/versions/`.
- `apps/web/src/` contains Next.js routes, styles, shared components, and API helpers. `packages/api-client/` contains generated TypeScript contracts.
- `deploy/` and `scripts/` contain deployment configuration and local tooling. Preserve the original workbook and Mockplus assets under `references/customer/`. Consult `README.md` and `项目上下文.md` for project status.

## Local Development and Build

- Follow `docs/project/本地开发.md` to configure local PostgreSQL. Install pinned dependencies with `uv sync --directory apps/server --frozen` and `pnpm --dir apps/web install --frozen-lockfile`.
- In `apps/server`, run `uv run alembic upgrade head` to migrate and `uv run uvicorn app.main:app` to start the API. Start the sync worker with `uv run python -m app.sync`.
- In `apps/web`, run `pnpm dev` for development, `pnpm typecheck` for TypeScript checks, and `pnpm build` for the production build.
- For the configured local Docker stack, run `python3 scripts/stack_ops.py up --build` from the root and open `http://127.0.0.1:3080`.

## Coding Style and API Contracts

Use four spaces and snake_case in Python. Run `uv run ruff check app tests` in `apps/server`. Use two spaces in TypeScript, PascalCase for React components, and kebab-case for shared module filenames. Follow `apps/web/AGENTS.md`.

After API changes, run `pnpm api:generate` and `pnpm api:check` in `apps/web`. Regenerate declarations from the API and keep unmodeled responses typed as unknown.

## Testing

Use `uv run pytest -q` in `apps/server` for `tests/test_*.py`, backed by dedicated local PostgreSQL. Run `pnpm exec playwright test` in `apps/web` against the seeded local app for `tests/*.spec.ts`. Cover relevant permission boundaries, cross-operator isolation, approval transitions, Decimal precision, and sync retries. Compare affected screens with the customer prototype.

## Commits and Pull Requests

Use short, action-oriented subjects with prefixes seen in history, such as `fix: align table card headings`, `feat:`, `docs:`, and `test:`. Describe scope, verification, linked issues when available, and migration or configuration changes. Include screenshots for visible UI changes.

## Configuration and Handoff

Keep secrets and real customer records out of Git. Public portal publishing requires explicit consent and `content.public`. Confirm substantive workflow changes against requirements and record material decisions in `项目上下文.md`.
