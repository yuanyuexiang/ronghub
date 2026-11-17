"""Structured reply documents use only images included in the same immutable submission."""

from copy import deepcopy

from fastapi import HTTPException
from pydantic import ValidationError

from app.richtext import RichDocument, image_ids, plain_text


def prepare(body, document):
    if document is None:
        text = body.strip()
        value = None
    else:
        value = document.model_dump(exclude_none=True)
        text = plain_text(value).strip()
    if not text or len(text) > 4000:
        raise HTTPException(422, "回复不能为空，可读内容最多4000字")
    return text, value


def parse(value):
    if value is None:
        return None
    if len(value.encode()) > 262144:
        raise HTTPException(422, "回复富文本结构最多256KiB")
    try:
        return RichDocument.model_validate_json(value)
    except ValidationError:
        raise HTTPException(422, "回复富文本格式无效") from None


def bind_images(document, identifiers):
    """Incoming privateImage IDs are 1-based positions in this request's files, never DB IDs."""
    if document is None:
        return None
    if not image_ids(document).issubset(set(range(1, len(identifiers) + 1))):
        raise HTTPException(422, "回复正文图片必须来自本次上传的图片序号")
    result = deepcopy(document)

    def visit(node):
        if node["type"] == "privateImage":
            node["attrs"]["attachment_id"] = identifiers[node["attrs"]["attachment_id"] - 1]
        for child in node.get("content", []):
            visit(child)

    for node in result["content"]:
        visit(node)
    return result
