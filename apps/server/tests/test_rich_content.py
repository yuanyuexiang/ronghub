import io
import secrets
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.main import app
from app.models import Article, ArticleAction, ArticleAttachment, now
from app.richtext import RichDocument

PNG = b"\x89PNG\r\n\x1a\nfixture"


def doc(*nodes):
    return {"type": "doc", "content": list(nodes)}


def paragraph(text="fixture"):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def image(identifier):
    return {"type": "privateImage", "attrs": {"attachment_id": identifier, "alt": "现场图片"}}


def office(kind="docx", **extra):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml" if kind == "docx" else "xl/workbook.xml", "<document/>"
        )
        for name, body in extra.items():
            archive.writestr(name, body)
    return stream.getvalue()


def login(name, password):
    client = TestClient(app, client=("rich-" + secrets.token_hex(8), 1))
    response = client.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert response.status_code == 200
    return client, {"x-csrf-token": response.json()["csrf_token"]}


def cleanup(ids):
    from app.config import settings

    with SessionLocal.begin() as db:
        for asset in db.scalars(
            select(ArticleAttachment).where(ArticleAttachment.article_id.in_(ids))
        ):
            (Path(settings.storage_path) / asset.storage_key).unlink(missing_ok=True)
        db.execute(delete(ArticleAttachment).where(ArticleAttachment.article_id.in_(ids)))
        db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(ids)))
        db.execute(delete(Article).where(Article.id.in_(ids)))


def test_document_whitelist_links_tables_depth_and_legacy_html_text():
    valid = doc(
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "发布标题"}],
        },
        paragraph("<script>alert(1)</script>"),
        {
            "type": "table",
            "content": [
                {
                    "type": "tableRow",
                    "content": [
                        {
                            "type": "tableHeader",
                            "attrs": {"colspan": 2, "rowspan": 1, "colwidth": None},
                            "content": [paragraph("项目")],
                        }
                    ],
                },
                {
                    "type": "tableRow",
                    "content": [
                        {"type": "tableCell", "content": [paragraph("场站")]},
                        {"type": "tableCell", "content": [paragraph("状态")]},
                    ],
                },
            ],
        },
    )
    assert (
        RichDocument.model_validate(valid).content[1].content[0].text == "<script>alert(1)</script>"
    )
    bad = [
        doc({"type": "html", "text": "<img onerror=alert(1)>"}),
        doc({"type": "paragraph", "attrs": {"style": "position:fixed"}, "content": []}),
        doc(image(True)),
        doc({"type": "privateImage", "attrs": {"attachment_id": 1, "src": "https://evil/image"}}),
        doc({"type": "heading", "attrs": {"level": 6}, "content": []}),
        doc(
            {
                "type": "orderedList",
                "attrs": {"type": []},
                "content": [{"type": "listItem", "content": [paragraph()]}],
            }
        ),
    ]
    for url in [
        "javascript:alert(1)",
        "data:text/html,x",
        "https://user:secret@example.com",
        "https://example.com\n.evil",
        "/api/v1/accounts",
        "https://good\\@evil",
    ]:
        bad.append(
            doc(
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "link",
                            "marks": [{"type": "link", "attrs": {"href": url}}],
                        }
                    ],
                }
            )
        )
    deep = paragraph()
    for _ in range(14):
        deep = {"type": "blockquote", "content": [deep]}
    bad.extend(
        [
            doc(deep),
            doc(paragraph("x" * 20001)),
            doc(*[{"type": "paragraph", "content": [{"type": "text", "text": "x"}] * 4}] * 1000),
            doc(
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "attrs": {"rowspan": 5},
                                    "content": [paragraph()],
                                }
                            ],
                        }
                    ],
                }
            ),
        ]
    )
    for value in bad:
        with pytest.raises(ValidationError):
            RichDocument.model_validate(value)
    result = RichDocument.model_validate(
        doc(
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "站点",
                        "marks": [
                            {
                                "type": "link",
                                "attrs": {
                                    "href": "https://example.com",
                                    "target": "_blank",
                                    "rel": "unsafe",
                                    "class": "arbitrary",
                                },
                            }
                        ],
                    }
                ],
            }
        )
    )
    assert result.content[0].content[0].marks[0].attrs == {"href": "https://example.com"}


def test_private_assets_canonical_document_target_schedule_integrity_and_freeze(
    accounts, staff, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    names, password, _ = accounts
    ids = []
    reg, headers = login(staff[0][0], password)
    own, _ = login(names[0], password)
    foreign, _ = login(names[1], password)
    association, _ = login(staff[0][1], password)
    try:
        oid = own.get("/api/v1/auth/me").json()["operator_id"]
        base = {
            "kind": "notice",
            "title": "Rich " + secrets.token_hex(6),
            "body": "ignored legacy cache",
            "rich_document": doc(paragraph("通知内容")),
            "audience": "operator",
            "operator_id": oid,
        }
        article = reg.post("/api/v1/content", headers=headers, json=base).json()
        aid = article["id"]
        ids.append(aid)
        url = f"/api/v1/content/{aid}"
        assert article["body"] == "通知内容" and article["version"] == 1

        def upload(name, data, version):
            return reg.post(
                url + "/attachments",
                headers=headers,
                data={"version": str(version)},
                files={"file": (name, data)},
            )

        assert upload("bad.svg", b"<svg/>", 1).status_code == 415
        assert upload("huge.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024), 1).status_code == 413
        assert (
            upload("bad.docx", office("docx", **{"word/vbaProject.bin": "bad"}), 1).status_code
            == 415
        )
        assert upload("bad.xlsx", office("xlsx", **{"../escape": "bad"}), 1).status_code == 415
        assert not list(tmp_path.iterdir())
        for version, (name, data) in enumerate(
            [
                ("proof.png", PNG),
                ("proof.pdf", b"%PDF-1.4proof"),
                ("proof.docx", office()),
                ("proof.xlsx", office("xlsx")),
            ],
            1,
        ):
            response = upload(name, data, version)
            assert response.status_code == 201, response.text
        details = reg.get(url + "?manage=true").json()
        assert details["version"] == 5 and len(details["attachments"]) == 4
        png = details["attachments"][0]
        pdf = details["attachments"][1]
        assert own.get(url + "/attachments/" + str(png["id"]) + "?inline=true").status_code == 404
        assert (
            foreign.get(url + "/attachments/" + str(png["id"]) + "?manage=true").status_code == 403
        )
        assert upload("stale.png", PNG, 1).status_code == 409
        other = reg.post("/api/v1/content", headers=headers, json={**base, "title": "Other"}).json()
        ids.append(other["id"])
        other_image = reg.post(
            f"/api/v1/content/{other['id']}/attachments",
            headers=headers,
            data={"version": "1"},
            files={"file": ("other.png", PNG)},
        ).json()["attachments"][0]
        rejected = reg.patch(
            url,
            headers=headers,
            json={
                **base,
                "rich_document": doc(paragraph(), image(other_image["id"])),
                "version": 5,
            },
        )
        assert rejected.status_code == 422
        deleted = reg.delete(
            f"/api/v1/content/{other['id']}/attachments/{other_image['id']}",
            headers=headers,
            params={"version": 2},
        )
        assert deleted.status_code == 200 and deleted.json()["attachments"] == []
        edited = reg.patch(
            url,
            headers=headers,
            json={
                **base,
                "rich_document": doc(paragraph("图文通知"), image(png["id"])),
                "version": 5,
            },
        )
        assert edited.status_code == 200 and edited.json()["version"] == 6
        assert (
            reg.delete(
                url + "/attachments/" + str(png["id"]), params={"version": 6}, headers=headers
            ).status_code
            == 409
        )
        # A valid earlier file must not become frozen if a later file fails integrity verification.
        with SessionLocal() as db:
            pdf_path = tmp_path / db.get(ArticleAttachment, pdf["id"]).storage_key
        original = pdf_path.read_bytes()
        pdf_path.write_bytes(b"x" * len(original))
        assert (
            reg.post(
                url + "/actions", headers=headers, json={"action": "publish", "version": 6}
            ).status_code
            == 409
        )
        assert all(
            not asset["frozen"] for asset in reg.get(url + "?manage=true").json()["attachments"]
        )
        pdf_path.write_bytes(original)
        published = reg.post(
            url + "/actions",
            headers=headers,
            json={
                "action": "publish",
                "version": 6,
                "published_at": (now() + timedelta(days=1)).isoformat(),
            },
        )
        assert published.status_code == 200
        assert own.get(url).status_code == 404
        assert own.get(url + f"/attachments/{png['id']}?inline=true").status_code == 404
        with SessionLocal.begin() as db:
            db.get(Article, aid).published_at = now() - timedelta(seconds=1)
        visible = own.get(url).json()
        assert len(visible["attachments"]) == 4 and all(
            asset["frozen"] for asset in visible["attachments"]
        )
        image_response = own.get(url + f"/attachments/{png['id']}?inline=true")
        assert (
            image_response.content == PNG
            and image_response.headers["cache-control"] == "no-store"
            and image_response.headers["content-security-policy"] == "sandbox"
        )
        assert (
            foreign.get(url + f"/attachments/{png['id']}?inline=true").status_code == 404
            and association.get(url).status_code == 404
        )
        assert reg.patch(url, headers=headers, json={**base, "version": 7}).status_code == 409
        assert upload("frozen.png", PNG, 7).status_code == 409
        assert (
            reg.delete(
                url + f"/attachments/{png['id']}", params={"version": 7}, headers=headers
            ).status_code
            == 409
        )
        history = reg.get(url + "?manage=true").json()["history"]
        frozen = next(row["snapshot"] for row in history if row["action"] == "publish")
        assert (
            len(frozen["attachments"]) == 4 and frozen["attachments"][0]["sha256"] == png["sha256"]
        )
        assert frozen["rich_document"]["content"][1]["attrs"]["attachment_id"] == png["id"]
        assert (
            reg.post(
                url + "/actions", headers=headers, json={"action": "withdraw", "version": 7}
            ).status_code
            == 200
        )
        assert own.get(url + f"/attachments/{png['id']}").status_code == 404
        assert reg.get(url + f"/attachments/{png['id']}?manage=true").status_code == 200
    finally:
        for c in [reg, own, foreign, association]:
            c.close()
        cleanup(ids)


def test_upload_publish_race_and_commit_failure_leave_no_orphans(
    accounts, staff, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    ids = []
    first, h1 = login(staff[0][0], accounts[1])
    second, h2 = login(staff[0][0], accounts[1])
    try:
        article = first.post(
            "/api/v1/content",
            headers=h1,
            json={"kind": "news", "title": "Race", "body": "legacy text"},
        ).json()
        aid = article["id"]
        ids.append(aid)
        url = f"/api/v1/content/{aid}"
        with SessionLocal() as db:
            assert (
                db.scalar(select(Article.rich_document.is_(None)).where(Article.id == aid)) is True
            )

        def publish():
            return first.post(
                url + "/actions", headers=h1, json={"action": "publish", "version": 1}
            ).status_code

        def upload():
            return second.post(
                url + "/attachments",
                headers=h2,
                data={"version": "1"},
                files={"file": ("race.png", PNG)},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(publish)
            b = pool.submit(upload)
            statuses = sorted([a.result(), b.result()])
        assert statuses in [[200, 409], [201, 409]]
        state = first.get(url + "?manage=true").json()
        assert state["version"] == 2
        assert (state["status"] == "published" and not state["attachments"]) or (
            state["status"] == "draft" and len(state["attachments"]) == 1
        )
        fresh = first.post(
            "/api/v1/content",
            headers=h1,
            json={"kind": "news", "title": "Rollback", "body": "text"},
        ).json()
        ids.append(fresh["id"])
        before = set(tmp_path.iterdir())
        original = Session.commit

        def fail(self):
            raise RuntimeError("Fixture commit failure")

        monkeypatch.setattr(Session, "commit", fail)
        with TestClient(app, raise_server_exceptions=False) as raw:
            raw.cookies.update(first.cookies)
            assert (
                raw.post(
                    f"/api/v1/content/{fresh['id']}/attachments",
                    headers=h1,
                    data={"version": "1"},
                    files={"file": ("rollback.png", PNG)},
                ).status_code
                == 500
            )
        monkeypatch.setattr(Session, "commit", original)
        assert set(tmp_path.iterdir()) == before
        assert first.get(f"/api/v1/content/{fresh['id']}?manage=true").json()["version"] == 1
    finally:
        first.close()
        second.close()
        cleanup(ids)
