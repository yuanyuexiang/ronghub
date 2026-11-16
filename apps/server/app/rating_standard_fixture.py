"""Remove only unused, explicitly named local browser-created standard versions."""

import argparse
import re

from sqlalchemy import delete, or_, select
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.models import Audit, RatingApplication, RatingStandard, RatingStandardDraft


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    args = parser.parse_args()
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "localhost",
        "127.0.0.1",
    }:
        raise SystemExit("Standard cleanup requires a local demo database")
    if not re.fullmatch(r"browser-config-[a-f0-9]{16}", args.code):
        raise SystemExit("Refusing non-fixture standard code")
    with SessionLocal.begin() as db:
        standards = list(
            db.scalars(
                select(RatingStandard)
                .where(RatingStandard.code == args.code, RatingStandard.is_demo.is_(True))
                .with_for_update()
            )
        )
        ids = [standard.id for standard in standards]
        if db.scalar(
            select(RatingApplication.id).where(RatingApplication.standard_id.in_(ids)).limit(1)
        ):
            raise SystemExit("Refusing cleanup while any application uses the standard")
        drafts = list(
            db.scalars(
                select(RatingStandardDraft)
                .where(
                    or_(
                        RatingStandardDraft.data["code"].as_string() == args.code,
                        RatingStandardDraft.data["name"].as_string() == args.code,
                    )
                )
                .with_for_update()
            )
        )
        if any(
            row.published_standard_id is not None and row.published_standard_id not in ids
            for row in drafts
        ):
            raise SystemExit("Refusing cleanup of a draft linked to another standard")
        draft_ids = [row.id for row in drafts]
        db.execute(
            delete(Audit).where(
                Audit.action.in_(
                    [
                        "ratings.standard_draft_create",
                        "ratings.standard_draft_edit",
                        "ratings.standard_draft_delete",
                        "ratings.standard_draft_finalize",
                    ]
                ),
                Audit.object_id.in_(draft_ids),
            )
        )
        for row in drafts:
            db.delete(row)
        db.flush()
        db.execute(
            delete(Audit).where(
                Audit.action.in_(
                    [
                        "ratings.standard_create",
                        "ratings.standard_availability",
                        "ratings.standard_archive",
                    ]
                ),
                Audit.object_id.in_(ids),
            )
        )
        for standard in standards:
            db.delete(standard)
    print("Removed only unused named browser fixture standards")


if __name__ == "__main__":
    main()
