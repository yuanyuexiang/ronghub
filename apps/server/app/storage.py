"""Bounded private file storage shared by business material routes."""

import hashlib
import secrets
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings

MAX_BYTES = 5 * 1024 * 1024


def store_upload(
    file: UploadFile,
    max_bytes: int = MAX_BYTES,
    allow_archives: bool = False,
    allowed_types: set[str] | None = None,
    allow_office: bool = False,
):
    filename = Path((file.filename or "材料").replace("\\", "/")).name
    filename = "".join(c for c in filename if c.isprintable())[:180] or "材料"
    base = Path(settings.storage_path).resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    key = secrets.token_hex(24)
    path = base / key
    size = 0
    digest = hashlib.sha256()
    first = True
    mime = None
    try:
        with path.open("xb") as stream:
            path.chmod(0o600)
            while chunk := file.file.read(65536):
                if first:
                    mime = (
                        "application/pdf"
                        if chunk.startswith(b"%PDF-")
                        else "image/png"
                        if chunk.startswith(b"\x89PNG\r\n\x1a\n")
                        else "image/jpeg"
                        if chunk.startswith(b"\xff\xd8\xff")
                        else None
                    )
                    if not mime and allow_office and chunk.startswith(b"PK\x03\x04"):
                        mime = {
                            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        }.get(Path(filename).suffix.lower())
                    if not mime and allow_archives:
                        if chunk.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                            mime = "application/zip"
                        elif chunk.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")):
                            mime = "application/vnd.rar"
                    if allow_office:
                        extensions = {
                            "application/pdf": {".pdf"},
                            "image/png": {".png"},
                            "image/jpeg": {".jpg", ".jpeg"},
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                                ".docx"
                            },
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                                ".xlsx"
                            },
                        }
                        if Path(filename).suffix.lower() not in extensions.get(mime, set()):
                            raise HTTPException(415, "附件扩展名与实际允许格式不符")
                    if allow_archives:
                        extensions = {
                            "application/pdf": {".pdf"},
                            "image/png": {".png"},
                            "image/jpeg": {".jpg", ".jpeg"},
                            "application/zip": {".zip"},
                            "application/vnd.rar": {".rar"},
                        }
                        if Path(filename).suffix.lower() not in extensions.get(mime, set()):
                            raise HTTPException(415, "文件扩展名与允许的材料格式不符")
                    if allowed_types is not None and mime not in allowed_types:
                        raise HTTPException(415, "该业务不支持此文件格式")
                    if not mime:
                        raise HTTPException(415, "不支持的材料格式，请按页面说明上传")
                    first = False
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, "文件超过当前允许的单份或剩余总量限制")
                digest.update(chunk)
                stream.write(chunk)
        if not size:
            raise HTTPException(422, "文件不能为空")
        if allow_office and mime in OFFICE_TYPES:
            validate_office(path, mime)
        return {
            "storage_key": key,
            "filename": filename,
            "content_type": mime,
            "size": size,
            "sha256": digest.hexdigest(),
        }, path
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


OFFICE_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def validate_office(path, mime):
    from pathlib import PurePosixPath

    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            required = "word/document.xml" if "wordprocessingml" in mime else "xl/workbook.xml"
            if (
                not 1 <= len(entries) <= 2000
                or required not in names
                or "[Content_Types].xml" not in names
            ):
                raise ValueError("Office container structure is invalid")
            if sum(entry.file_size for entry in entries) > 50 * 1024 * 1024:
                raise ValueError("Office expanded size exceeds bounds")
            for entry in entries:
                relative = PurePosixPath(entry.filename)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in entry.filename
                    or entry.flag_bits & 1
                    or "vbaproject" in entry.filename.lower()
                    or (
                        entry.file_size > 1024 * 1024
                        and entry.file_size > 100 * max(1, entry.compress_size)
                    )
                ):
                    raise ValueError("Office archive contains unsupported entries")
            if archive.testzip() is not None:
                raise ValueError("Office archive CRC mismatch")
            if len(names) != len(entries):
                raise ValueError("Duplicate Office archive entries")
    except (zipfile.BadZipFile, ValueError, RuntimeError) as error:
        raise HTTPException(
            415, "Office附件须为有效且有界的非加密DOCX/XLSX文件，不支持宏或异常压缩包"
        ) from error
