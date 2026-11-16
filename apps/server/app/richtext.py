"""Bounded Tiptap-compatible document tree. No user HTML, remote images or arbitrary attributes."""

import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

BLOCKS = {
    "paragraph",
    "heading",
    "bulletList",
    "orderedList",
    "blockquote",
    "codeBlock",
    "horizontalRule",
    "table",
    "privateImage",
}


class RichMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bold", "italic", "underline", "strike", "code", "link"]
    attrs: dict[str, JsonValue] | None = None


class RichNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "paragraph",
        "heading",
        "text",
        "hardBreak",
        "bulletList",
        "orderedList",
        "listItem",
        "blockquote",
        "codeBlock",
        "horizontalRule",
        "table",
        "tableRow",
        "tableCell",
        "tableHeader",
        "privateImage",
    ]
    text: str | None = Field(None, max_length=20000)
    attrs: dict[str, JsonValue] | None = None
    marks: list[RichMark] | None = Field(None, max_length=6)
    content: list["RichNode"] | None = Field(None, max_length=1000)


class RichDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["doc"]
    content: list[RichNode] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def bounded_tree(self):
        data = self.model_dump(exclude_none=True)
        if len(json.dumps(data, ensure_ascii=False).encode()) > 262144:
            raise ValueError("富文本结构最多256KiB")
        budget = [0, 0]
        for node in data["content"]:
            if node["type"] not in BLOCKS:
                raise ValueError("正文根节点必须是块级内容")
            validate_node(node, 1, budget)
        plain = plain_text(data)
        if not plain.strip() or len(plain) > 20000:
            raise ValueError("正文不能为空，含换行的可读内容最多20000字")
        # Canonical attributes only; editor default target/class/column widths never control rendering.
        self.content = [RichNode.model_validate(node) for node in data["content"]]
        return self


def positive(value, maximum):
    return type(value) is int and 1 <= value <= maximum


def validate_node(node, depth, budget):
    budget[0] += 1
    if depth > 12 or budget[0] > 3000:
        raise ValueError("富文本层级最多12层、3000节点")
    kind = node["type"]
    attrs = node.get("attrs") or {}
    children = node.get("content") or []
    allowed_attrs = {
        "heading": {"level"},
        "orderedList": {"start", "type"},
        "codeBlock": {"language"},
        "tableCell": {"colspan", "rowspan", "colwidth"},
        "tableHeader": {"colspan", "rowspan", "colwidth"},
        "privateImage": {"attachment_id", "alt"},
    }.get(kind, set())
    if set(attrs) - allowed_attrs:
        raise ValueError("富文本包含不允许的属性")
    if kind != "text" and (node.get("text") is not None or node.get("marks")):
        raise ValueError("文字与格式标记只能属于文本节点")
    if kind == "text":
        if not node.get("text") or children or attrs:
            raise ValueError("文本节点结构无效")
        seen = set()
        for mark in node.get("marks", []):
            if mark["type"] in seen:
                raise ValueError("重复的文字格式")
            seen.add(mark["type"])
            options = mark.get("attrs") or {}
            if mark["type"] == "link":
                if set(options) - {"href", "target", "rel", "class"}:
                    raise ValueError("链接属性无效")
                href = options.get("href")
                if (
                    not isinstance(href, str)
                    or len(href) > 2000
                    or href != href.strip()
                    or any(ord(c) < 33 for c in href)
                ):
                    raise ValueError("链接仅支持完整HTTP/HTTPS地址")
                try:
                    url = urlsplit(href)
                except ValueError as error:
                    raise ValueError("链接地址无效") from error
                if (
                    url.scheme not in {"https", "http"}
                    or not url.netloc
                    or url.username
                    or url.password
                    or "\\" in href
                ):
                    raise ValueError("链接仅支持不含凭据的HTTP/HTTPS地址")
                mark["attrs"] = {"href": href}
            elif options:
                raise ValueError("格式标记不支持自定义属性")
    elif kind in {"paragraph", "heading"}:
        if any(child["type"] not in {"text", "hardBreak"} for child in children):
            raise ValueError("段落只能含文字和换行")
        if kind == "heading" and not positive(attrs.get("level"), 3):
            raise ValueError("标题仅支持1至3级")
    elif kind in {"bulletList", "orderedList"}:
        if not children or any(child["type"] != "listItem" for child in children):
            raise ValueError("列表须包含列表项")
        if kind == "orderedList":
            if not positive(attrs.get("start", 1), 100000) or attrs.get("type") not in (None, "1"):
                raise ValueError("有序列表起始或编号格式无效")
            node["attrs"] = {"start": attrs.get("start", 1)}
    elif kind == "listItem":
        if (
            not children
            or children[0]["type"] != "paragraph"
            or any(child["type"] not in BLOCKS for child in children)
        ):
            raise ValueError("列表项须以段落开头")
    elif kind == "blockquote":
        if not children or any(child["type"] not in BLOCKS for child in children):
            raise ValueError("引用须包含段落等块级内容")
    elif kind == "codeBlock":
        if any(child["type"] != "text" or child.get("marks") for child in children):
            raise ValueError("代码块只能包含纯文本")
        if attrs.get("language") is not None and (
            not isinstance(attrs["language"], str) or len(attrs["language"]) > 30
        ):
            raise ValueError("代码语言标记无效")
    elif kind in {"horizontalRule", "hardBreak", "privateImage"}:
        if children:
            raise ValueError("图片、分隔线和换行不能包含子内容")
        if kind == "privateImage":
            budget[1] += 1
            if (
                budget[1] > 10
                or not positive(attrs.get("attachment_id"), 2147483647)
                or not isinstance(attrs.get("alt", ""), str)
                or len(attrs.get("alt", "")) > 200
            ):
                raise ValueError("正文图片最多10处，须引用本内容的图片附件")
    elif kind == "table":
        if not 1 <= len(children) <= 100 or any(child["type"] != "tableRow" for child in children):
            raise ValueError("表格最多100行")
        grid = {}
        width = None
        for index, row in enumerate(children):
            if not row.get("content"):
                raise ValueError("表格行不能为空")
            col = 0
            for cell in row["content"]:
                while (index, col) in grid:
                    col += 1
                opts = cell.get("attrs") or {}
                cols = opts.get("colspan", 1)
                rows = opts.get("rowspan", 1)
                if (
                    not positive(cols, 20)
                    or not positive(rows, 100)
                    or col + cols > 20
                    or index + rows > len(children)
                ):
                    raise ValueError("表格合并范围或列数无效")
                for y in range(index, index + rows):
                    for x in range(col, col + cols):
                        if (y, x) in grid:
                            raise ValueError("表格合并单元格重叠")
                        grid[y, x] = True
                col += cols
            row_width = max((x for y, x in grid if y == index), default=-1) + 1
            if width is not None and row_width != width:
                raise ValueError("表格各行列数不一致")
            width = row_width
        if any((y, x) not in grid for y in range(len(children)) for x in range(width or 0)):
            raise ValueError("表格存在空缺单元格")
    elif kind == "tableRow":
        if not children or any(
            child["type"] not in {"tableCell", "tableHeader"} for child in children
        ):
            raise ValueError("表格行只能含单元格")
    elif kind in {"tableCell", "tableHeader"}:
        if not children or any(child["type"] not in BLOCKS - {"table"} for child in children):
            raise ValueError("单元格须包含段落，不支持嵌套表格")
        if not positive(attrs.get("colspan", 1), 20) or not positive(attrs.get("rowspan", 1), 100):
            raise ValueError("单元格范围无效")
        node["attrs"] = {"colspan": attrs.get("colspan", 1), "rowspan": attrs.get("rowspan", 1)}
    for child in children:
        validate_node(child, depth + 1, budget)


def plain_text(node):
    kind = node["type"]
    if kind == "text":
        return node["text"]
    if kind == "privateImage":
        return "[图片] " + str((node.get("attrs") or {}).get("alt", ""))
    if kind == "hardBreak":
        return "\n"
    if kind == "horizontalRule":
        return "——"
    children = node.get("content") or []
    return ("" if kind in {"paragraph", "heading", "codeBlock"} else "\n").join(
        plain_text(child) for child in children
    )


def image_ids(node):
    result = set()
    if node["type"] == "privateImage":
        result.add(node["attrs"]["attachment_id"])
    for child in node.get("content") or []:
        result.update(image_ids(child))
    return result
