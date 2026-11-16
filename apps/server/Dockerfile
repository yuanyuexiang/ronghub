FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
WORKDIR /app

FROM base AS dependencies
RUN pip install --no-cache-dir uv==0.10.0
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
COPY apps/server/pyproject.toml apps/server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS runtime
RUN groupadd --gid 10001 ronghub && useradd --uid 10001 --gid 10001 --no-create-home ronghub \
    && mkdir -p /var/lib/ronghub/attachments && chown 10001:10001 /var/lib/ronghub/attachments
COPY --from=dependencies /opt/venv /opt/venv
COPY apps/server/app ./app
COPY apps/server/alembic ./alembic
COPY apps/server/alembic.ini ./
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
