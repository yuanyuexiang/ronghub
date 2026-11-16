"""Trusted local demo SMS preparation; there is no HTTP code/answer endpoint."""

import argparse
import json
import secrets

from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.dependencies import token_hash
from app.models import SmsChallenge, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("token")
    parser.add_argument("--container-preview", action="store_true")
    args = parser.parse_args()
    if (
        not settings.demo_mode
        or settings.sms_mode != "demo"
        or (
            make_url(settings.database_url).host not in {"localhost", "127.0.0.1"}
            and not args.container_preview
        )
    ):
        raise SystemExit("SMS fixtures require explicitly enabled local/container demo mode")
    with SessionLocal.begin() as db:
        row = db.get(SmsChallenge, token_hash(args.token), with_for_update=True)
        if (
            not row
            or row.user_id is None
            or not row.phone
            or row.consumed_at is not None
            or row.expires_at <= now()
            or row.attempts >= 5
            or row.delivery_mode != "demo"
        ):
            raise SystemExit("Refusing to alter an absent, expired or exhausted demo SMS challenge")
        code = f"{secrets.randbelow(1000000):06d}"
        row.code_hash = token_hash(args.token + ":" + code)
    print(json.dumps({"code": code, "delivery_mode": "demo"}))


if __name__ == "__main__":
    main()
