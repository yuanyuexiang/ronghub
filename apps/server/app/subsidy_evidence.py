"""Subsidy statement and opaque ZIP evidence, serialized on the claim lock."""

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.business_audit import state, transition
from app.config import settings
from app.material_archive import read_material
from app.models import SubsidyAction, SubsidyAttachment

LIMIT = 20 * 1024 * 1024
MAX_FILES = 10


def validate_note(note):
    if not isinstance(note, str) or len(note) > 2000 or "\x00" in note:
        raise HTTPException(422, "申请说明须为不超过2000字且不含空字符的文本")


def writable(user, claim, version):
    if user.role != "operator" or claim.operator_id != user.operator_id:
        raise HTTPException(403, "仅所属运营商可维护补贴申请资料")
    if claim.status != "draft":
        raise HTTPException(409, "仅草稿可维护申请资料，已提交证据不可更改")
    if claim.version != version:
        raise HTTPException(409, "申请版本已更新，请重新打开详情核对")


def metadata(record):
    return {
        "id": record.id,
        "filename": record.filename,
        "size": record.size,
        "sha256": record.sha256,
        "content_type": record.content_type,
        "created_at": record.created_at.isoformat(),
        "submitted_version": record.submitted_version,
    }


def content(record):
    if not 0 < record.size <= LIMIT or record.content_type != "application/zip":
        raise HTTPException(409, "补贴材料大小或类型不正确，操作未提交")
    try:
        return read_material(Path(settings.storage_path).resolve(), record)
    except HTTPException as error:
        raise HTTPException(409, "补贴材料缺失或完整性校验失败，操作未提交") from error


def changed(db, user, claim, action, before):
    claim.version += 1
    db.add(
        SubsidyAction(
            claim_id=claim.id,
            actor_id=user.id,
            action=action,
            comment={
                "edit": "修改申请说明",
                "material_upload": "上传申报ZIP材料",
                "material_delete": "删除未提交材料",
            }[action],
            delta=0,
            transition_snapshot={"before": before, "after": state(claim)},
        )
    )
    transition(db, user, "subsidy." + action, claim.id, before, state(claim), "subsidy_claim")


def edit(db, user, claim, version, note):
    writable(user, claim, version)
    validate_note(note)
    before = state(claim)
    claim.application_note = note
    changed(db, user, claim, "edit", before)


def freeze(db, claim):
    if not claim.application_note or not claim.application_note.strip():
        raise HTTPException(422, "提交前请完善申请说明")
    validate_note(claim.application_note)
    records = list(
        db.scalars(
            select(SubsidyAttachment)
            .where(SubsidyAttachment.claim_id == claim.id)
            .order_by(SubsidyAttachment.id)
        )
    )
    if not records:
        raise HTTPException(422, "提交前请上传ZIP申报材料")
    if len(records) > MAX_FILES or sum(record.size for record in records) > LIMIT:
        raise HTTPException(409, "申报材料超出数量或总量限制")
    for record in records:
        content(record)
        record.submitted_version = claim.version + 1
    claim.snapshot = {
        **(claim.snapshot or {}),
        "submission": {
            "version": claim.version + 1,
            "note": claim.application_note,
            "materials": [metadata(record) for record in records],
        },
    }


def verify_submission(db, claim):
    packet = (claim.snapshot or {}).get("submission")
    if packet is None:
        # Old submitted records have no invented packet. New submissions use freeze().
        return
    if not isinstance(packet, dict) or packet.get("note") != claim.application_note:
        raise HTTPException(409, "申请说明与已提交快照不一致")
    records = list(
        db.scalars(
            select(SubsidyAttachment)
            .where(SubsidyAttachment.claim_id == claim.id)
            .order_by(SubsidyAttachment.id)
        )
    )
    if not records or len(records) > MAX_FILES or sum(record.size for record in records) > LIMIT:
        raise HTTPException(409, "已提交材料集合不完整")
    if [metadata(record) for record in records] != packet.get("materials") or any(
        record.submitted_version != packet.get("version") for record in records
    ):
        raise HTTPException(409, "材料与已提交快照不一致")
    for record in records:
        content(record)
