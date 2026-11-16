"""Consistent read sessions for multi-query reports; never changes the engine default."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import engine


def snapshot_session():
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        Session(bind=connection, expire_on_commit=False) as session,
    ):
        yield session


SnapshotDB = Annotated[Session, Depends(snapshot_session)]
