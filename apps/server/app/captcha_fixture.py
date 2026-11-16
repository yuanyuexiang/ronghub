"""Trusted test setup only: explicit local/demo DB access, never an HTTP answer endpoint."""

import argparse
import json

from sqlalchemy.engine import make_url

from app.captcha import image
from app.config import settings
from app.db import SessionLocal
from app.dependencies import token_hash
from app.models import CaptchaChallenge, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("token")
    parser.add_argument("--container-preview", action="store_true")
    args = parser.parse_args()
    if not settings.demo_mode or (
        make_url(settings.database_url).host not in {"localhost", "127.0.0.1"}
        and not args.container_preview
    ):
        raise SystemExit("Captcha fixtures require an explicitly local or container demo database")
    with SessionLocal.begin() as db:
        row = db.get(CaptchaChallenge, token_hash(args.token), with_for_update=True)
        if not row or row.consumed_at is not None or row.expires_at <= now():
            raise SystemExit("Refusing to alter an absent, expired or consumed challenge")
        answer = "ABCDE"
        row.answer_hash = token_hash(args.token + ":" + answer)
    print(json.dumps({"answer": answer, "image": image(answer)}))


if __name__ == "__main__":
    main()
