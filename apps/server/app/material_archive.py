"""Bounded, deterministic private archives. Uploaded ZIP/RAR contents remain opaque."""

import hashlib
import io
import json
import os
import re
import stat
import zipfile

from fastapi import HTTPException


def read_material(base, record):
    if not re.fullmatch(r"[a-f0-9]{48}", record.storage_key) or not re.fullmatch(
        r"[a-f0-9]{64}", record.sha256
    ):
        raise HTTPException(409, "材料完整性校验失败，请联系管理员核查")
    try:
        descriptor = os.open(base / record.storage_key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != record.size:
                raise OSError("Material size or type mismatch")
            content = stream.read(record.size + 1)
        if len(content) != record.size or hashlib.sha256(content).hexdigest() != record.sha256:
            raise OSError("Material digest mismatch")
        return content
    except OSError as error:
        raise HTTPException(409, "材料缺失或完整性校验失败，未生成部分压缩包") from error


def archive(base, application, records, limit):
    if not records:
        raise HTTPException(409, "当前申请暂无可下载材料")
    if (
        len(records) > 30
        or any(record.size <= 0 or record.size > limit for record in records)
        or sum(record.size for record in records) > limit
    ):
        raise HTTPException(409, "材料数量或总量超出打包限制，请联系管理员核查")
    output = io.BytesIO()
    manifest = {
        "application_id": application.id,
        "application_version": application.version,
        "is_demo": application.is_demo,
        "materials": [],
    }

    def write(bundle, entry, content):
        info = zipfile.ZipInfo(entry, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        # No recompression, extraction, executable flags, host paths or current timestamps.
        bundle.writestr(info, content)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as bundle:
        for record in records:
            name = record.filename.replace("\\", "/").rsplit("/", 1)[-1]
            name = (
                "".join(char for char in name if char.isprintable() and char not in '<>:"|?*')[
                    :160
                ].strip(" .")
                or "material"
            )
            entry = f"materials/{record.id}-{name}"
            write(bundle, entry, read_material(base, record))
            manifest["materials"].append(
                {
                    "id": record.id,
                    "entry": entry,
                    "filename": record.filename,
                    "size": record.size,
                    "sha256": record.sha256,
                    "content_type": record.content_type,
                    "category": record.category,
                    "submitted_version": record.submitted_version,
                }
            )
        write(
            bundle,
            "manifest.json",
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
        )
    return output.getvalue()
