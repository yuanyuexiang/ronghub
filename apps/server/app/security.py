"""Persistent fixed-window login throttling; do not trust forwarded headers here."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert

from app.dependencies import token_hash
from app.models import LoginThrottle, now


def throttle(db, username: str, address: str):
    current = now()
    for label, value, limit in [("ip", address, 40), ("account", username, 5)]:
        key = token_hash(label + ":" + value)
        expired = LoginThrottle.window_end <= current
        count = db.scalar(
            insert(LoginThrottle)
            .values(key=key, attempts=1, window_end=current + timedelta(minutes=15))
            .on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "attempts": case((expired, 1), else_=LoginThrottle.attempts + 1),
                    "window_end": case(
                        (expired, current + timedelta(minutes=15)), else_=LoginThrottle.window_end
                    ),
                },
            )
            .returning(LoginThrottle.attempts)
        )
        db.commit()
        if count > limit:
            raise HTTPException(429, "尝试次数过多，请15分钟后重试", headers={"Retry-After": "900"})


def clear_account(db, username):
    row = db.scalar(
        select(LoginThrottle)
        .where(LoginThrottle.key == token_hash("account:" + username))
        .with_for_update()
    )
    if row:
        row.attempts = 0
