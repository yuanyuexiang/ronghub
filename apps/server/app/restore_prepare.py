"""Owner-only removal of an empty current schema before restoring an older archive."""

import os

from sqlalchemy import MetaData, Table, func, inspect, select, text

from app import models  # noqa: F401 — register every application table
from app.db import Base, engine


def prepare(connection):
    """Keep extensions and unrelated objects; refuse unknown tables and all business data."""
    schema = connection.scalar(text("SELECT current_schema()"))
    names = set(inspect(connection).get_table_names(schema=schema))
    expected = set(Base.metadata.tables) | {"alembic_version"}
    if names != expected:
        raise ValueError("Restore refused: target must have the complete current schema only")
    quote = connection.dialect.identifier_preparer.quote
    tables = ", ".join(f"{quote(schema)}.{quote(name)}" for name in sorted(names))
    connection.execute(text(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE"))
    for table in Base.metadata.sorted_tables:
        if connection.scalar(select(func.count()).select_from(table)):
            raise ValueError("Restore refused: target contains business records")
    # No CASCADE: an unmanaged dependent object must fail rather than be destroyed.
    Base.metadata.drop_all(connection)
    Table("alembic_version", MetaData(), autoload_with=connection).drop(connection)


def main():
    if os.environ.get("RONGHUB_STACK_MIGRATION") != "true":
        raise SystemExit("Restore preparation requires the privileged stack migration job")
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        prepare(connection)
    print("Empty application tables removed; extensions and unrelated objects retained.")


if __name__ == "__main__":
    main()
