"""Offline maintenance checks used by coordinated stack backup/restore, not a public API."""

import argparse
import hashlib
import re
from pathlib import Path

from sqlalchemy import delete, func, select

from app.config import settings
from app.db import Base, SessionLocal
from app.models import (
    ArticleAttachment,
    ArticleReplyAttachment,
    Attachment,
    EnrollmentSession,
    InspectionAttachment,
    LoginSession,
    RatingAttachment,
    RegistrationAttachment,
    SubsidyAttachment,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-empty", action="store_true")
    parser.add_argument("--revoke-sessions", action="store_true")
    args = parser.parse_args()
    storage = Path(settings.storage_path)
    with SessionLocal.begin() as db:
        if args.require_empty:
            if any(
                db.scalar(select(func.count()).select_from(table))
                for table in Base.metadata.sorted_tables
            ):
                raise SystemExit("Restore refused: target contains business records")
            if storage.exists() and any(storage.iterdir()):
                raise SystemExit("Restore refused: target attachment directory is not empty")
            print("Empty restore target verified.")
            return
        checked = 0
        for model in [
            Attachment,
            InspectionAttachment,
            RatingAttachment,
            SubsidyAttachment,
            RegistrationAttachment,
            ArticleReplyAttachment,
            ArticleAttachment,
        ]:
            for record in db.scalars(select(model)):
                if not re.fullmatch(r"[a-f0-9]{48}", record.storage_key):
                    raise SystemExit(f"Invalid storage key: {model.__tablename__} #{record.id}")
                path = storage / record.storage_key
                if not path.is_file() or path.is_symlink() or path.stat().st_size != record.size:
                    raise SystemExit(
                        f"Missing/invalid material: {model.__tablename__} #{record.id}"
                    )
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != record.sha256:
                    raise SystemExit(
                        f"Material digest mismatch: {model.__tablename__} #{record.id}"
                    )
                checked += 1
        if args.revoke_sessions:
            from app.models import CaptchaChallenge, SmsChallenge, StaffApplicantSession

            db.execute(delete(CaptchaChallenge))
            db.execute(delete(SmsChallenge))
            db.execute(delete(StaffApplicantSession))
            db.execute(delete(LoginSession))
            db.execute(delete(EnrollmentSession))
        print(
            f"Verified {checked} referenced private files; sessions {'revoked' if args.revoke_sessions else 'unchanged'}."
        )


if __name__ == "__main__":
    main()
