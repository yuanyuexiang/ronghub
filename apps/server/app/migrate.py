"""Stack-only privileged migration job; long-running services have no DDL privileges."""

import os

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.db import engine


def main():
    if os.environ.get("RONGHUB_STACK_MIGRATION") != "true":
        raise SystemExit(
            "Use alembic directly for local development; this entrypoint is stack-only"
        )
    command.upgrade(Config("alembic.ini"), "head")
    with engine.begin() as connection:
        connection.execute(
            text("REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM ronghub_app")
        )
    print("Schema ready; application role cannot modify migration history.")


if __name__ == "__main__":
    main()
