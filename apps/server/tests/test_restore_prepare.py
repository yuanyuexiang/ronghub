import secrets

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app.db import Base, engine
from app.restore_prepare import prepare


@pytest.fixture
def empty_schema():
    # All DDL stays in this transaction; rollback removes only this random schema.
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = "restore_verify_" + secrets.token_hex(8)
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        Base.metadata.create_all(connection, checkfirst=False)
        connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('0040')"))
        yield connection, schema
        transaction.rollback()


def test_empty_current_tables_can_be_removed_without_dropping_extensions(empty_schema):
    connection, schema = empty_schema
    extension = connection.scalar(text("SELECT oid FROM pg_extension WHERE extname='btree_gist'"))
    assert extension
    prepare(connection)
    assert inspect(connection).get_table_names(schema=schema) == []
    assert (
        connection.scalar(text("SELECT oid FROM pg_extension WHERE extname='btree_gist'"))
        == extension
    )


@pytest.mark.parametrize("obstacle", ["business", "unknown", "dependent_view"])
def test_preparation_refuses_data_and_unmanaged_objects_atomically(empty_schema, obstacle):
    connection, schema = empty_schema
    if obstacle == "business":
        connection.execute(text("INSERT INTO operators(name) VALUES ('preserve-business')"))
    elif obstacle == "unknown":
        connection.execute(text("CREATE TABLE unmanaged_records (id integer)"))
    else:
        connection.execute(text("CREATE VIEW unmanaged_view AS SELECT id FROM operators"))
    before = set(inspect(connection).get_table_names(schema=schema))
    with connection.begin_nested() as savepoint:
        with pytest.raises((ValueError, DBAPIError)):
            prepare(connection)
        savepoint.rollback()
    assert set(inspect(connection).get_table_names(schema=schema)) == before
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0040"
    if obstacle == "business":
        assert connection.scalar(text("SELECT name FROM operators")) == "preserve-business"
    if obstacle == "dependent_view":
        assert inspect(connection).get_view_names(schema=schema) == ["unmanaged_view"]
