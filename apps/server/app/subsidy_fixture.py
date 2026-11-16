"""Explicit local demo evidence for browser scenarios; never a business API fallback."""

import io
import zipfile

from fastapi import UploadFile
from sqlalchemy.engine import make_url

from app.config import settings
from app.models import SubsidyAttachment
from app.storage import store_upload


def seed(db, claim, user):
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "localhost",
        "127.0.0.1",
    }:
        raise RuntimeError("Subsidy samples require a local demo database")
    claim.application_note = "浏览器模拟申报说明（非真实资料）"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr(
            zipfile.ZipInfo("sample.txt", date_time=(1980, 1, 1, 0, 0, 0)), "模拟申报ZIP资料"
        )
    stream.seek(0)
    values, _path = store_upload(
        UploadFile(file=stream, filename="模拟申报材料.zip"),
        allow_archives=True,
        allowed_types={"application/zip"},
    )
    db.add(SubsidyAttachment(claim_id=claim.id, uploaded_by=user.id, **values))
    db.flush()
