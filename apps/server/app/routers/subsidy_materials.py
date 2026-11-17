"""Versioned subsidy drafts and private ZIP evidence."""

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app import subsidy
from app import subsidy_evidence as evidence
from app.authorization import authorize_business_write
from app.business_audit import state
from app.business_contracts import (
    SubsidyClaimResponse,
    SubsidyMaterialDeleted,
    SubsidyMaterialList,
    SubsidyMaterialMutation,
)
from app.config import settings
from app.dependencies import DB, Actor, SnapshotActor
from app.material_contracts import binary_responses
from app.models import SubsidyAttachment
from app.reporting import SnapshotDB
from app.storage import store_upload

router = APIRouter(prefix="/api/v1/subsidy/claims", tags=["subsidy"])
Id = Annotated[int, Field(gt=0, le=2147483647)]
Version = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class SubsidyDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Version
    application_note: str = Field(max_length=2000)


@router.patch("/{claim_id}", response_model=SubsidyClaimResponse)
def edit(claim_id: Id, body: SubsidyDraftInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "subsidy.edit")
    subsidy.demo_only()
    claim = subsidy.claim_access(db, user, claim_id, True)
    evidence.edit(db, user, claim, body.version, body.application_note)
    result = SubsidyClaimResponse.model_validate(subsidy.output(claim))
    db.commit()
    return result


@router.get("/{claim_id}/attachments", response_model=SubsidyMaterialList)
def listing(claim_id: Id, user: SnapshotActor, db: SnapshotDB):
    claim = subsidy.claim_access(db, user, claim_id)
    records = list(
        db.scalars(
            select(SubsidyAttachment)
            .where(SubsidyAttachment.claim_id == claim.id)
            .order_by(SubsidyAttachment.id)
        )
    )
    return {
        "version": claim.version,
        "items": [evidence.metadata(record) for record in records],
        "total_bytes": sum(record.size for record in records),
        "limit_bytes": evidence.LIMIT,
    }


@router.post("/{claim_id}/attachments", status_code=201, response_model=SubsidyMaterialMutation)
def upload(
    claim_id: Id,
    file: UploadFile,
    user: Actor,
    db: DB,
    request: Request,
    version: Annotated[int, Form(ge=1, le=2147483647)],
):
    authorize_business_write(db, user, request, "subsidy.materials")
    subsidy.demo_only()
    claim = subsidy.claim_access(db, user, claim_id, True)
    evidence.writable(user, claim, version)
    records = list(
        db.scalars(select(SubsidyAttachment).where(SubsidyAttachment.claim_id == claim.id))
    )
    if len(records) >= evidence.MAX_FILES:
        raise HTTPException(422, "每份申请最多10份ZIP材料")
    remaining = evidence.LIMIT - sum(record.size for record in records)
    if remaining <= 0:
        raise HTTPException(413, "申报材料总量不能超过20MiB")
    values, path = store_upload(
        file, max_bytes=remaining, allow_archives=True, allowed_types={"application/zip"}
    )
    try:
        before = state(claim)
        material = SubsidyAttachment(claim_id=claim.id, uploaded_by=user.id, **values)
        db.add(material)
        evidence.changed(db, user, claim, "material_upload", before)
        db.flush()
        result = SubsidyMaterialMutation.model_validate(
            {"version": claim.version, "material": evidence.metadata(material)}
        )
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return result


@router.delete("/{claim_id}/attachments/{material_id}", response_model=SubsidyMaterialDeleted)
def remove(
    claim_id: Id,
    material_id: Id,
    version: Annotated[int, Field(ge=1, le=2147483647)],
    user: Actor,
    db: DB,
    request: Request,
):
    authorize_business_write(db, user, request, "subsidy.materials")
    subsidy.demo_only()
    claim = subsidy.claim_access(db, user, claim_id, True)
    evidence.writable(user, claim, version)
    record = db.get(SubsidyAttachment, material_id)
    if not record or record.claim_id != claim.id:
        raise HTTPException(404, "材料不存在")
    if record.submitted_version is not None:
        raise HTTPException(409, "已提交证据不可删除")
    if not re.fullmatch(r"[a-f0-9]{48}", record.storage_key):
        raise HTTPException(409, "材料存储标识异常，请联系管理员")
    before = state(claim)
    path = Path(settings.storage_path).resolve() / record.storage_key
    db.delete(record)
    evidence.changed(db, user, claim, "material_delete", before)
    result = SubsidyMaterialDeleted(ok=True, version=claim.version)
    db.commit()
    path.unlink(missing_ok=True)
    return result


@router.get(
    "/{claim_id}/attachments/{material_id}/download",
    response_class=Response,
    responses=binary_responses("application/zip"),
)
def download(claim_id: Id, material_id: Id, user: Actor, db: DB):
    subsidy.claim_access(db, user, claim_id, True)
    record = db.get(SubsidyAttachment, material_id)
    if not record or record.claim_id != claim_id:
        raise HTTPException(404, "材料不存在")
    body = evidence.content(record)
    headers = {
        "Content-Disposition": "attachment; filename*=utf-8''" + quote(record.filename),
        "Cache-Control": "no-store",
        "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    db.commit()
    return Response(body, media_type="application/zip", headers=headers)
