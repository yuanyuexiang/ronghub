from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app import enrollment
from app.config import settings
from app.dependencies import DB, DUMMY_PASSWORD_HASH, passwords, token_hash
from app.models import (
    EnrollmentSession,
    Operator,
    Registration,
    RegistrationAction,
    RegistrationAttachment,
    User,
)
from app.routers.attachments import metadata
from app.routers.companies import ContactInput
from app.security import clear_account, throttle
from app.storage import store_upload

router = APIRouter(prefix="/api/v1/enrollment", tags=["enrollment"])


class DataInput(BaseModel):
    company_name: str = Field(min_length=1, max_length=150)
    credit_code: str = Field(pattern=r"^[0-9A-Z]{18}$")
    registration_address: str = Field(default="", max_length=255)
    contacts: list[ContactInput] = Field(min_length=1, max_length=10)

    @field_validator("company_name")
    @classmethod
    def nonblank(cls, v):
        if not v.strip():
            raise ValueError("企业名称不能为空")
        return v.strip()

    @field_validator("credit_code", mode="before")
    @classmethod
    def code(cls, v):
        return str(v).strip().upper()

    @model_validator(mode="after")
    def primary(self):
        if sum(c.primary for c in self.contacts) > 1:
            raise ValueError("最多设置一个主要联系人")
        return self


class CreateInput(DataInput):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=200)


class EditInput(DataInput):
    version: int = Field(ge=1)


class LoginInput(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class SubmitInput(BaseModel):
    version: int = Field(ge=1)


def history(db, r):
    return [
        {"action": a.action, "comment": a.comment, "created_at": a.created_at.isoformat()}
        for a in db.scalars(
            select(RegistrationAction)
            .where(RegistrationAction.registration_id == r.id)
            .order_by(RegistrationAction.id)
        )
    ]


def check_existing(db, body):
    if db.scalar(select(User.id).where(User.username == body.username)) or db.scalar(
        select(Operator.id).where(
            (Operator.name == body.company_name) | (Operator.credit_code == body.credit_code)
        )
    ):
        raise HTTPException(409, "账号或企业已存在，请联系平台管理员")


@router.post("", status_code=201)
def create(body: CreateInput, request: Request, response: Response, db: DB):
    enrollment.demo_only()
    throttle(
        db,
        "register:" + body.username,
        "register:" + (request.client.host if request.client else "unknown"),
    )
    check_existing(db, body)
    r = Registration(
        **body.model_dump(exclude={"password"}), password_hash=passwords.hash(body.password)
    )
    db.add(r)
    try:
        db.flush()
        enrollment.record(db, r, "create")
        csrf = enrollment.establish(db, response, r)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "已有相同账号或企业的注册申请，请登录进度查询") from e
    return {**enrollment.output(r), "csrf_token": csrf}


@router.post("/login")
def login(body: LoginInput, request: Request, response: Response, db: DB):
    enrollment.demo_only()
    throttle(
        db,
        "enroll:" + body.username,
        "enroll:" + (request.client.host if request.client else "unknown"),
    )
    r = db.scalar(
        select(Registration).where(Registration.username == body.username).with_for_update()
    )
    user = (
        db.scalar(select(User).where(User.id == r.user_id).with_for_update(key_share=True))
        if r and r.user_id
        else None
    )
    hashed = user.password_hash if user else r.password_hash if r else DUMMY_PASSWORD_HASH
    valid = passwords.verify(body.password, hashed)
    company = db.get(Operator, r.operator_id) if r and r.operator_id else None
    if (
        not r
        or not valid
        or (
            r.status == "approved"
            and (not user or not user.enabled or not company or not company.enabled)
        )
    ):
        raise HTTPException(401, "注册账号或密码错误")
    if user and user.must_change_password:
        raise HTTPException(403, "请先登录业务平台修改临时密码")
    clear_account(db, "enroll:" + body.username)
    csrf = enrollment.establish(db, response, r)
    db.commit()
    return {**enrollment.output(r), "csrf_token": csrf}


@router.get("/me")
def me(request: Request, r: enrollment.Applicant, db: DB):
    session = db.get(EnrollmentSession, token_hash(request.cookies[enrollment.COOKIE]))
    return {**enrollment.output(r), "csrf_token": session.csrf, "history": history(db, r)}


@router.post("/logout")
def logout(request: Request, response: Response, r: enrollment.Applicant, db: DB):
    session = db.get(EnrollmentSession, token_hash(request.cookies[enrollment.COOKIE]))
    db.delete(session)
    db.commit()
    response.delete_cookie(enrollment.COOKIE, path="/api/v1/enrollment")
    return {"ok": True}


@router.patch("/me")
def edit(body: EditInput, r: enrollment.Applicant, db: DB):
    r = enrollment.lock(db, r)
    enrollment.editable(r)
    if r.version != body.version:
        raise HTTPException(409, "资料已更新，请刷新")
    for key, value in body.model_dump(exclude={"version"}).items():
        setattr(r, key, value)
    r.version += 1
    try:
        enrollment.record(db, r, "edit")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "已有相同企业注册申请") from e
    return enrollment.output(r)


@router.post("/me/submit")
def submit(body: SubmitInput, r: enrollment.Applicant, db: DB):
    r = enrollment.lock(db, r)
    enrollment.editable(r)
    if r.version != body.version:
        raise HTTPException(409, "资料已更新，请刷新")
    if not db.scalar(
        select(RegistrationAttachment.id)
        .where(RegistrationAttachment.registration_id == r.id)
        .limit(1)
    ):
        raise HTTPException(422, "演示注册要求至少一份资质材料，正式清单待确认")
    r.status = "submitted"
    r.version += 1
    db.execute(
        update(RegistrationAttachment)
        .where(
            RegistrationAttachment.registration_id == r.id,
            RegistrationAttachment.submitted_version.is_(None),
        )
        .values(submitted_version=r.version)
    )
    enrollment.record(db, r, "submit")
    db.commit()
    return enrollment.output(r)


@router.get("/me/attachments")
def attachments(r: enrollment.Applicant, db: DB):
    return {
        "items": [
            {**metadata(a), "submitted_version": a.submitted_version}
            for a in db.scalars(
                select(RegistrationAttachment)
                .where(RegistrationAttachment.registration_id == r.id)
                .order_by(RegistrationAttachment.id)
            )
        ]
    }


@router.post("/me/attachments", status_code=201)
def upload(file: UploadFile, r: enrollment.Applicant, db: DB):
    r = enrollment.lock(db, r)
    enrollment.editable(r)
    if (
        db.scalar(
            select(func.count())
            .select_from(RegistrationAttachment)
            .where(RegistrationAttachment.registration_id == r.id)
        )
        >= 10
    ):
        raise HTTPException(422, "每份注册最多10份材料")
    values, path = store_upload(file)
    try:
        a = RegistrationAttachment(registration_id=r.id, **values)
        db.add(a)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return metadata(a)


@router.get("/me/attachments/{material_id}/download")
def download(material_id: int, r: enrollment.Applicant, db: DB):
    a = db.get(RegistrationAttachment, material_id)
    if not a or a.registration_id != r.id:
        raise HTTPException(404, "材料不存在")
    path = Path(settings.storage_path).resolve() / a.storage_key
    if not path.is_file():
        raise HTTPException(404, "材料文件不可用")
    return FileResponse(
        path,
        filename=a.filename,
        media_type=a.content_type,
        headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/me/attachments/{material_id}")
def remove(material_id: int, r: enrollment.Applicant, db: DB):
    r = enrollment.lock(db, r)
    enrollment.editable(r)
    a = db.get(RegistrationAttachment, material_id)
    if not a or a.registration_id != r.id:
        raise HTTPException(404, "材料不存在")
    if a.submitted_version is not None:
        raise HTTPException(409, "已提交的资质证据不可删除，可补充新材料")
    path = Path(settings.storage_path).resolve() / a.storage_key
    db.delete(a)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
