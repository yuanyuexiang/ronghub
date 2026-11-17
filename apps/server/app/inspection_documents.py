"""Task-specific documents and explicit operator-reported execution facts."""

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.models import InspectionAttachment, now
from app.richtext import RichDocument, image_ids, plain_text


class ExecutionContact(BaseModel):
    name: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def pair(self):
        self.name = (self.name.strip() or None) if self.name else None
        self.phone = (self.phone.strip() or None) if self.phone else None
        if bool(self.name) != bool(self.phone):
            raise ValueError("执行联系人及联系方式需同时填写")
        if any(ord(c) < 32 for c in (self.name or "") + (self.phone or "")):
            raise ValueError("联系信息不能包含控制字符")
        return self


def prepare(text: str, document: RichDocument | None):
    value = document.model_dump(exclude_none=True) if document is not None else None
    readable = plain_text(value).strip() if value is not None else text.strip()
    if not readable or len(readable) > 4000:
        raise HTTPException(422, "巡检要求或处理说明不能为空，可读内容最多4000字")
    return readable, value


def requirement(text, document):
    readable, value = prepare(text, document)
    if value is not None and image_ids(value):
        raise HTTPException(422, "任务要求暂不支持嵌入图片，请使用文字、列表或表格")
    return readable, value


def reply(db, task, action, text, document, contact, performed_at):
    readable, value = prepare(text, document)
    if action != "reply":
        if contact is not None or performed_at is not None:
            raise HTTPException(422, "执行联系信息与填报时间仅用于企业整改回复")
        if value is not None and image_ids(value):
            raise HTTPException(422, "复核说明不支持嵌入材料图片")
    else:
        # Historical missing facts stay readable; every new reply needs explicit facts.
        if contact is None or not contact.name or not contact.phone or performed_at is None:
            raise HTTPException(422, "本次整改回复需填写执行联系人、联系方式及用户填报执行时间")
        if (
            db.scalar(
                select(InspectionAttachment.id)
                .where(
                    InspectionAttachment.task_id == task.id,
                    InspectionAttachment.action_id.is_(None),
                )
                .limit(1)
            )
            is None
        ):
            raise HTTPException(422, "本次整改回复至少需一份未提交的证明材料")
        if performed_at is not None and (
            performed_at.tzinfo is None or performed_at.utcoffset() is None or performed_at > now()
        ):
            raise HTTPException(422, "用户填报执行时间需带时区且不能晚于当前时间")
        if value is not None:
            requested = image_ids(value)
            allowed = set(
                db.scalars(
                    select(InspectionAttachment.id).where(
                        InspectionAttachment.task_id == task.id,
                        InspectionAttachment.action_id.is_(None),
                        InspectionAttachment.content_type.in_({"image/png", "image/jpeg"}),
                        InspectionAttachment.id.in_(requested),
                    )
                )
            )
            if requested != allowed:
                raise HTTPException(422, "反馈图片必须来自本任务本次未提交的PNG/JPEG材料")
    return readable, value
