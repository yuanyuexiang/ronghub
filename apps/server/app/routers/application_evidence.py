"""Private, bounded application review content and retained submission materials."""

from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import application_evidence as evidence
from app.authorization import authorize_business_write
from app.config import settings
from app.dependencies import DB, Actor, SnapshotActor
from app.material_archive import archive, read_material
from app.material_contracts import ApplicationAttachmentItem, binary_responses
from app.models import ApplicationSubmission, ApprovalAction, Attachment
from app.reporting import SnapshotDB
from app.workflows import allowed_demo_rules

router = APIRouter(prefix="/api/v1/applications", tags=["application-evidence"])


@router.get("/{application_id}/review-context", response_model=evidence.ReviewContext)
def context(application_id: int, user: SnapshotActor, db: SnapshotDB):
    return evidence.context(db, user, evidence.access(db, user, application_id))


@router.get("/{application_id}/submissions", response_model=evidence.SubmissionPage)
def listing(
    application_id: int,
    user: SnapshotActor,
    db: SnapshotDB,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(10, ge=1, le=100),
):
    evidence.access(db, user, application_id)
    stmt = select(ApplicationSubmission).where(
        ApplicationSubmission.application_id == application_id
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(ApplicationSubmission.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [evidence.submission_item(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def find_submission(db, user, application_id, submission_id):
    a = evidence.access(db, user, application_id)
    row = db.scalar(
        select(ApplicationSubmission).where(
            ApplicationSubmission.application_id == a.id, ApplicationSubmission.id == submission_id
        )
    )
    if not row:
        raise HTTPException(404, "送审版本不存在或不可访问")
    return a, row


@router.get(
    "/{application_id}/submissions/{submission_id}", response_model=evidence.SubmissionDetail
)
def detail(application_id: int, submission_id: int, user: SnapshotActor, db: SnapshotDB):
    _a, row = find_submission(db, user, application_id, submission_id)
    return evidence.submission_detail(db, user, row)


class CategoryInput(BaseModel):
    revision: int = Field(ge=1)
    version: int = Field(ge=1)
    category: str | None = Field(None, max_length=40)


class CategoryResult(BaseModel):
    application_id: int
    application_version: int
    material: ApplicationAttachmentItem


@router.patch(
    "/{application_id}/attachments/{attachment_id}/category",
    response_model=CategoryResult,
)
def category(
    application_id: int,
    attachment_id: int,
    body: CategoryInput,
    user: Actor,
    db: DB,
    request: Request,
):
    authorize_business_write(db, user, request, "applications.materials")
    allowed_demo_rules()
    a = evidence.access(db, user, application_id, lock=True)
    if (
        user.role != "operator"
        or a.status not in {"draft", "rejected"}
        or a.version != body.version
    ):
        raise HTTPException(409, "申请不在可维护状态或已更新，请重新读取")
    if body.category is not None and body.category not in evidence.CATEGORIES:
        raise HTTPException(422, "不支持的申请材料分类")
    material = db.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.application_id == a.id,
            Attachment.active.is_(True),
        )
    )
    if not material:
        raise HTTPException(404, "材料不存在或不属于当前申请")
    from app.business_audit import transition

    if material.revision != body.revision:
        raise HTTPException(409, "材料分类已被更新，请重新读取材料")
    previous = {"kind": material.category, "version": material.revision}
    material.category = body.category
    material.revision += 1
    db.add(
        ApprovalAction(
            application_id=a.id,
            actor_id=user.id,
            action="material_category",
            comment="更新材料分类",
        )
    )
    transition(
        db,
        user,
        "applications.materials.category",
        material.id,
        previous,
        {"kind": material.category, "version": material.revision},
        "application_material",
    )
    result = CategoryResult(
        application_id=a.id,
        application_version=a.version,
        material=ApplicationAttachmentItem(
            **evidence.material(material), revision=material.revision
        ),
    )
    db.commit()
    return result


def frozen_material(db, row, attachment_id):
    metadata = next((m for m in row.snapshot["materials"] if m["id"] == attachment_id), None)
    stored = db.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id, Attachment.application_id == row.application_id
        )
    )
    if not metadata or not stored:
        raise HTTPException(404, "此送审版本未留存该材料")
    if any(
        getattr(stored, key) != metadata[key]
        for key in ["filename", "sha256", "size", "content_type"]
    ):
        raise HTTPException(409, "历史材料与冻结清单不一致，请联系管理员")
    return SimpleNamespace(
        **metadata, storage_key=stored.storage_key, submitted_version=row.submitted_version
    )


@router.get(
    "/{application_id}/submissions/{submission_id}/materials/{attachment_id}/download",
    response_class=Response,
    responses=binary_responses("application/pdf", "image/png", "image/jpeg"),
)
def historical_file(
    application_id: int, submission_id: int, attachment_id: int, user: SnapshotActor, db: SnapshotDB
):
    _a, row = find_submission(db, user, application_id, submission_id)
    material = frozen_material(db, row, attachment_id)
    body = read_material(Path(settings.storage_path).resolve(), material)
    from urllib.parse import quote

    return Response(
        body,
        media_type=material.content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(material.filename, safe=""),
        },
    )


@router.get(
    "/{application_id}/submissions/{submission_id}/archive",
    response_class=Response,
    responses=binary_responses("application/zip"),
)
def historical_archive(
    application_id: int, submission_id: int, user: SnapshotActor, db: SnapshotDB
):
    a, row = find_submission(db, user, application_id, submission_id)
    records = [frozen_material(db, row, m["id"]) for m in row.snapshot["materials"]]
    body = archive(
        Path(settings.storage_path).resolve(),
        SimpleNamespace(
            id=a.id, version=row.submitted_version, is_demo=a.rule_version.startswith("demo")
        ),
        records,
        50 * 1024 * 1024,
    )
    return Response(
        body,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="APP-{a.id}-submission-{row.id}.zip"',
        },
    )


@router.get(
    "/{application_id}/attachments/archive",
    response_class=Response,
    responses=binary_responses("application/zip"),
)
def current_archive(application_id: int, user: SnapshotActor, db: SnapshotDB):
    a = evidence.access(db, user, application_id)
    records = [
        SimpleNamespace(**evidence.material(m), storage_key=m.storage_key, submitted_version=None)
        for m in db.scalars(
            select(Attachment)
            .where(Attachment.application_id == a.id, Attachment.active.is_(True))
            .order_by(Attachment.id)
        )
    ]
    body = archive(
        Path(settings.storage_path).resolve(),
        SimpleNamespace(id=a.id, version=a.version, is_demo=a.rule_version.startswith("demo")),
        records,
        50 * 1024 * 1024,
    )
    return Response(
        body,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="APP-{a.id}-materials.zip"',
        },
    )
