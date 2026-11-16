"""Explicit valid statement/ZIP samples for financial tests, not a runtime bypass."""

import io
import zipfile

from fastapi import UploadFile

from app import subsidy
from app.models import SubsidyAttachment
from app.storage import store_upload


def evidence(db, claim, user):
    claim.application_note = "测试场站的真实输入样本（非生产申报）"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("sample.txt", "本测试申请的模拟资料")
    stream.seek(0)
    values, _path = store_upload(
        UploadFile(file=stream, filename="申报样本.zip"),
        allow_archives=True,
        allowed_types={"application/zip"},
    )
    record = SubsidyAttachment(claim_id=claim.id, uploaded_by=user.id, **values)
    db.add(record)
    db.flush()
    return record


def create_claim(db, user, *args, **kwargs):
    claim = subsidy.create_claim(db, user, *args, **kwargs)
    evidence(db, claim, user)
    return claim
