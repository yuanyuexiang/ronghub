"""Exercise two isolated local Docker stacks, including real DB+attachment recovery.

Build images first: python3 scripts/stack_ops.py up --build
This check creates only random ronghub-verify-* / ronghub-restore-* fixtures and removes their volumes.
"""

import base64
import csv
import hashlib
import http.cookiejar
import io
import json
import secrets
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta
from uuid import uuid4
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from stack_ops import ROOT, Stack, initialize


def xlsx_cells(payload):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        strings = ["".join(value.itertext()) for value in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)
        result = []
        for index, sheet in enumerate(sheets, 1):
            rows = []
            for cell in ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml")).findall(".//s:c", ns):
                value = cell.find("s:v", ns)
                if value is not None:
                    rows.append((cell.get("r"), cell.get("t", "n"), strings[int(value.text)] if cell.get("t") == "s" else value.text))
                assert cell.find("s:f", ns) is None
            result.append((sheet.get("name"), rows))
        return result


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    def __init__(self, origin, stack):
        self.stack = stack
        self.origin = origin
        self.cookies = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = ""
        self.enrollment_csrf = ""

    def request(self, path, body=None, method=None, raw=None, content_type=None, expected=200,
                expected_media=None, private=False):
        headers = {}
        if body is not None:
            raw = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        if body is not None or raw is not None or method in {"POST", "PATCH", "DELETE"}:
            headers.update({"x-csrf-token": self.csrf, "Origin": self.origin})
        if self.enrollment_csrf and path.startswith("/api/v1/enrollment"):
            headers["x-enrollment-csrf"] = self.enrollment_csrf
        request = urllib.request.Request(
            self.origin + path, data=raw, headers=headers, method=method
        )
        try:
            response = self.http.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        status, data = response.code, response.read()
        if status != expected:
            raise AssertionError(f"Unexpected HTTP {status}, expected {expected}: {path}")
        if expected_media is not None:
            assert response.headers.get("Content-Type", "").split(";")[0] == expected_media
        if private:
            assert response.headers.get("Cache-Control") == "no-store"
        return json.loads(data) if "json" in response.headers.get("Content-Type", "") else data

    def login(self, name, password, expected=200):
        challenge = self.request("/api/v1/auth/captcha")
        seeded = json.loads(self.stack.run("exec", "-T", "api", "python", "-m", "app.captcha_fixture", "--container-preview", "--", challenge["captcha_id"], capture=True).stdout)
        result = self.request("/api/v1/auth/login", {"username": name, "password": password, "captcha_id":challenge["captcha_id"], "captcha_answer":seeded["answer"]}, expected=expected)
        if expected == 200:
            self.csrf=result["csrf_token"]

    def prepare_subsidy(self, claim):
        path=f"/api/v1/subsidy/claims/{claim['id']}"
        edited=self.request(path,{"version":claim["version"],"application_note":"Demo statement and opaque ZIP evidence; not real customer materials"},method="PATCH")
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w") as bundle:
            bundle.writestr(zipfile.ZipInfo("proof.txt", date_time=(1980,1,1,0,0,0)),"Demo evidence")
        raw,content_type=multipart(stream.getvalue(),"subsidy-proof.zip","application/zip",{"version":str(edited["version"])})
        self.request(path+"/attachments",raw=raw,content_type=content_type,expected=201)
        return self.request(path)

    def sms_challenge(self, identifier, purpose, kind="account"):
        challenge=self.request("/api/v1/auth/captcha")
        seed=json.loads(self.stack.run("exec","-T","api","python","-m","app.captcha_fixture","--container-preview","--",challenge["captcha_id"],capture=True).stdout)
        issued=self.request("/api/v1/auth/sms/challenges",{"purpose":purpose,"identifier_kind":kind,"identifier":identifier,"captcha_id":challenge["captcha_id"],"captcha_answer":seed["answer"]},expected=201)
        otp=json.loads(self.stack.run("exec","-T","api","python","-m","app.sms_fixture","--container-preview","--",issued["challenge_id"],capture=True).stdout)
        return issued,otp["code"]

    def staff_request(self, role, name, phone):
        captcha=self.request("/api/v1/auth/captcha")
        seed=json.loads(self.stack.run("exec","-T","api","python","-m","app.captcha_fixture","--container-preview","--",captcha["captcha_id"],capture=True).stdout)
        return self.request("/api/v1/auth/staff-enrollment",{"requested_role":role,"applicant_name":name,"contact_phone":phone,"captcha_id":captcha["captcha_id"],"captcha_answer":seed["answer"]},expected=201)


def multipart(content, filename="recovery-proof.pdf", mime="application/pdf", fields=None, file_field="file"):
    boundary = "ronghub-" + secrets.token_hex(12)
    prefix = b""
    for name, value in (fields or {}).items():
        prefix += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    data = (
        prefix
        + (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return data, "multipart/form-data; boundary=" + boundary


def prepare_inspection_plans(reg,owner,association,plan_targets,key):
    plan_title="Synthetic inspection plan "+key
    plan_draft=reg.request("/api/v1/inspection-plans",{"request_key":str(uuid4())},expected=201)
    plan_draft_path=f"/api/v1/inspection-plans/{plan_draft['id']}"
    plan_body={"version":1,"title":plan_title,"description":"Restorable plan requirements","rich_document":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Restorable plan requirements","marks":[{"type":"bold"}]}]}]},"station_ids":plan_targets,"due_at":None}
    plan_draft_before=reg.request(plan_draft_path,plan_body,method="PUT")
    assert plan_draft_before["status"]=="draft" and plan_draft_before["target_count"]==2 and plan_draft_before["due_at"] is None
    owner.request(plan_draft_path,expected=403)
    association.request(plan_draft_path,expected=403)
    assert owner.request("/api/v1/inspections?q="+urllib.parse.quote(plan_title))["total"]==0
    published_plan=reg.request("/api/v1/inspection-plans",{"request_key":str(uuid4())},expected=201)
    published_plan_path=f"/api/v1/inspection-plans/{published_plan['id']}"
    reg.request(published_plan_path,{**plan_body,"title":"Published "+plan_title},method="PUT")
    published_plan_before=reg.request(published_plan_path+"/publish",{"version":2})
    assert published_plan_before["published_count"]==2
    assert reg.request(published_plan_path+"/publish",{"version":2})==published_plan_before
    for planned_target in published_plan_before["targets"]:
        plan_task=reg.request(f"/api/v1/inspections/{planned_target['task_id']}")
        assert plan_task["plan_id"]==published_plan["id"] and plan_task["due_at"] is None and plan_task["overdue"] is False
    return {"draft_path":plan_draft_path,"draft":plan_draft_before,"published_path":published_plan_path,"published":published_plan_before}


def verify_restored_inspection_plans(restored_manager,restored,restored_association,proof):
    assert restored_manager.request(proof["draft_path"],private=True)==proof["draft"]
    assert restored_manager.request(proof["published_path"],private=True)==proof["published"]
    assert restored_manager.request(proof["published_path"]+"/publish",{"version":2})==proof["published"]
    resumed_plan=restored_manager.request(proof["draft_path"]+"/publish",{"version":2})
    assert resumed_plan["status"]=="published" and resumed_plan["published_count"]==2
    assert restored_manager.request(proof["draft_path"]+"/publish",{"version":2})==resumed_plan
    for planned in resumed_plan["targets"]:
        plan_task=restored_manager.request(f"/api/v1/inspections/{planned['task_id']}")
        assert plan_task["plan_id"]==proof["draft"]["id"] and plan_task["due_at"] is None and plan_task["overdue"] is False
        restored_association.request(f"/api/v1/inspections/{planned['task_id']}",expected=403)
    restored.request(proof["draft_path"],expected=403)
    print("Inspection plan restore: creator-private drafts, target recipient snapshots, no-deadline tasks and exact publication receipts preserved; pending batch published once after restore, repeat returns same tasks.",flush=True)


def prepare_inspection_query_proof(owner,other,association,task_detail):
    created_date=datetime.fromisoformat(task_detail["created_at"]).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    query=urllib.parse.urlencode({"record_id":task_detail["id"],"created_from":created_date,"created_to":created_date,"execution_state":"reported"})
    list_path="/api/v1/inspections?"+query
    csv_path="/api/v1/inspections/export?"+query
    view=owner.request(list_path,private=True)
    view.pop("query_at",None)
    assert view["total"]==1 and view["items"][0]["execution_state"]=="reported"
    assert view["items"][0]["last_reported_performed_at"] is not None
    content=owner.request(csv_path,expected_media="text/csv",private=True)
    decoded=list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert len(decoded)==1 and decoded[0]["该次执行联系人"]=="Synthetic executor"
    assert other.request(list_path)["total"]==0
    assert list(csv.DictReader(io.StringIO(other.request(csv_path,expected_media="text/csv",private=True).decode("utf-8-sig"))))==[]
    association.request(csv_path,expected=403)
    return {"list_path":list_path,"csv_path":csv_path,"view":view,"csv":content}


def verify_restored_inspection_query(owner,proof):
    view=owner.request(proof["list_path"],private=True)
    view.pop("query_at",None)
    assert view==proof["view"]
    assert owner.request(proof["csv_path"],expected_media="text/csv",private=True)==proof["csv"]
    print("Inspection query restore: applied Beijing creation range, actual feedback state/latest reply clocks and exact private full-query CSV preserved; no submitted clock substituted for user execution time.",flush=True)


def prepare_hazard_proof(reg,owner,other,association,station,key):
    created=reg.request("/api/v1/hazards",{"station_id":station["id"],"operator_id":station["operator_id"],"title":"Synthetic hazard "+key,"description":"Independent hazard supervision recovery","severity":"major","due_at":(datetime.now(ZoneInfo("UTC"))+timedelta(days=2)).isoformat()},expected=201)
    path="/api/v1/hazards/"+str(created["id"])
    started=owner.request(path+"/actions",{"version":created["version"],"action":"start","comment":"Synthetic start"})
    raw,mime=multipart(b"%PDF-1.4\nSynthetic hazard recovery proof","hazard-proof.pdf")
    material=owner.request(path+"/attachments",raw=raw,content_type=mime,expected=201)
    facts={"contact":{"name":"Synthetic hazard executor","phone":"13800000003"},"performed_at":(datetime.now(ZoneInfo("UTC"))-timedelta(hours=2)).isoformat()}
    completed=owner.request(path+"/actions",{"version":started["version"],"action":"reply","comment":"Synthetic completion; tracking continues",**facts})
    assert completed["progress"]=="completed" and completed["status"]=="tracking" and completed["closed_at"] is None
    raw,mime=multipart(b"%PDF-1.4\nSynthetic pending hazard supplement","hazard-pending.pdf")
    pending=owner.request(path+"/attachments",raw=raw,content_type=mime,expected=201)
    detail=owner.request(path,private=True)
    csv_path="/api/v1/hazards/export?"+urllib.parse.urlencode({"record_id":created["id"],"progress":"completed","status":"tracking"})
    report=owner.request(csv_path,expected_media="text/csv",private=True)
    other.request(path,expected=404)
    association.request(path,expected=403)
    files={}
    for item in [material,pending]:
        download=path+"/attachments/"+str(item["id"])+"/download"
        files[download]=hashlib.sha256(owner.request(download,expected_media="application/pdf",private=True)).hexdigest()
    return {"path":path,"detail":detail,"csv_path":csv_path,"csv":report,"files":files,"facts":facts}


def verify_restored_hazard(reg,owner,other,association,proof):
    path=proof["path"]
    assert owner.request(path,private=True)==proof["detail"]
    assert owner.request(proof["csv_path"],expected_media="text/csv",private=True)==proof["csv"]
    for download,digest in proof["files"].items():
        assert hashlib.sha256(owner.request(download,expected_media="application/pdf",private=True)).hexdigest()==digest
    other.request(path,expected=404)
    association.request(path,expected=403)
    before=proof["detail"]
    owner.request(path+"/actions",{"version":before["version"],"action":"reply","comment":"Incomplete new hazard reply"},expected=422)
    assert owner.request(path)==before
    continued=owner.request(path+"/actions",{"version":before["version"],"action":"reply","comment":"Complete restored supplement",**proof["facts"]})
    assert continued["progress"]=="completed" and continued["status"]=="tracking"
    closed=reg.request(path+"/actions",{"version":continued["version"],"action":"close","comment":"Explicit restored supervision closure"})
    assert closed["status"]=="closed" and closed["progress"]=="completed" and closed["closed_at"] is not None
    owner.request(path+"/actions",{"version":closed["version"],"action":"reply","comment":"Must not reopen closed supervision",**proof["facts"]},expected=409)
    print("Hazard restore: independent completed/tracking state, explicit facts, frozen/pending proof and exact private CSV preserved; complete supplement continued, explicit regulator closure stopped tracking without rewriting completion facts.",flush=True)


def prepare_protocol_proof(reg,owner,association,key):
    text="Synthetic protocol for recovery, not a formal customer standard"
    document={"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":text,"marks":[{"type":"bold"}]}]}]}
    created=reg.request("/api/v1/content",{"kind":"protocol","title":"Synthetic customer protocol "+key,"body":text,"rich_document":document},expected=201)
    path="/api/v1/content/"+str(created["id"])
    raw,mime=multipart(b"%PDF-1.4\nSynthetic protocol recovery document","protocol.pdf",fields={"version":str(created["version"])})
    uploaded=reg.request(path+"/attachments",raw=raw,content_type=mime,expected=201)
    published=reg.request(path+"/actions",{"version":uploaded["article"]["version"],"action":"publish"})
    reading="/api/v1/protocols/"+str(created["id"])
    detail=owner.request(reading,private=True)
    material=detail["attachments"][0]
    assert material["frozen"] and detail["rich_document"] is not None
    download=path+"/attachments/"+str(material["id"])
    payload=owner.request(download,expected_media="application/pdf",private=True)
    draft=reg.request("/api/v1/content",{"kind":"protocol","title":"Synthetic pending protocol "+key,"body":"Synthetic pending protocol; must remain hidden before publish"},expected=201)
    for actor in [reg,owner,association]:
        assert actor.request(reading)==detail
        actor.request("/api/v1/protocols/"+str(draft["id"]),expected=404)
    return {"path":path,"reading":reading,"detail":detail,"download":download,"digest":hashlib.sha256(payload).hexdigest(),"published":published,"draft":draft}


def verify_restored_protocol(reg,owner,association,proof):
    for actor in [reg,owner,association]:
        assert actor.request(proof["reading"],private=True)==proof["detail"]
        assert hashlib.sha256(actor.request(proof["download"],expected_media="application/pdf",private=True)).hexdigest()==proof["digest"]
        actor.request("/api/v1/protocols/"+str(proof["draft"]["id"]),expected=404)
    draft=proof["draft"]
    reg.request("/api/v1/content/"+str(draft["id"])+"/actions",{"version":draft["version"],"action":"publish"})
    assert owner.request("/api/v1/protocols/"+str(draft["id"]))["body"]==draft["body"]
    reg.request(proof["path"]+"/actions",{"version":proof["published"]["version"],"action":"withdraw"})
    for actor in [reg,owner,association]:
        actor.request(proof["reading"],expected=404)
        actor.request(proof["download"],expected=404)
    print("Protocol restore: published formatting/frozen private PDF and pending draft preserved; restored draft published explicitly and withdrawal revoked reader/download visibility for all three roles; no formal text invented.",flush=True)


def prepare_public_portal(reg,private_id,png,key):
    created=reg.request("/api/v1/content",{"kind":"news","title":"Explicit public recovery "+key,"body":"Explicitly approved synthetic public information","portal_public":True},expected=201)
    path="/api/v1/content/"+str(created["id"])
    latest=created
    materials=[]
    for filename,payload,mime_type in [("public-image.png",png,"image/png"),("public-document.pdf",b"%PDF-1.4\nSynthetic public recovery document","application/pdf")]:
        raw,mime=multipart(payload,filename,mime_type,{"version":str(latest["version"])})
        receipt=reg.request(path+"/attachments",raw=raw,content_type=mime,expected=201)
        latest=receipt["article"]
        materials=receipt["attachments"]
    image=next(item for item in materials if item["content_type"]=="image/png")
    document={"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":created["body"],"marks":[{"type":"bold"}]}]},{"type":"privateImage","attrs":{"attachment_id":image["id"],"alt":"Explicit public demo image"}}]}
    latest=reg.request(path,{"kind":"news","title":created["title"],"body":created["body"],"portal_public":True,"version":latest["version"],"rich_document":document},method="PATCH")
    published=reg.request(path+"/actions",{"action":"publish","version":latest["version"]})
    anonymous=Client(reg.origin,reg.stack)
    anonymous.request("/api/v1/auth/me",expected=401)
    info=anonymous.request("/api/v1/public/info",private=True)
    assert info["demo_mode"] is True
    reading="/api/v1/public/articles/"+str(created["id"])
    detail=anonymous.request(reading,private=True)
    assert "operator_id" not in detail and "history" not in detail and len(detail["attachments"])==2
    listing="/api/v1/public/articles?"+urllib.parse.urlencode({"q":created["title"]})
    page=anonymous.request(listing,private=True)
    assert page["total"]==1
    files={}
    for item in materials:
        download=reading+"/attachments/"+str(item["id"])
        files[download]=hashlib.sha256(anonymous.request(download,expected_media=item["content_type"],private=True)).hexdigest()
    draft=reg.request("/api/v1/content",{"kind":"announcement","title":"Pending explicit public "+key,"body":"Pending explicit public draft, not yet visible","portal_public":True},expected=201)
    anonymous.request("/api/v1/public/articles/"+str(draft["id"]),expected=404)
    anonymous.request("/api/v1/public/articles/"+str(private_id),expected=404)
    return {"path":path,"reading":reading,"detail":detail,"listing":listing,"page":page,"files":files,"published":published,"draft":draft,"private_id":private_id}


def verify_restored_public_portal(reg,proof):
    anonymous=Client(reg.origin,reg.stack)
    anonymous.request("/api/v1/auth/me",expected=401)
    assert anonymous.request(proof["reading"],private=True)==proof["detail"]
    assert anonymous.request(proof["listing"],private=True)==proof["page"]
    for download,digest in proof["files"].items():
        assert hashlib.sha256(anonymous.request(download,private=True)).hexdigest()==digest
    anonymous.request("/api/v1/public/articles/"+str(proof["private_id"]),expected=404)
    draft=proof["draft"]
    anonymous.request("/api/v1/public/articles/"+str(draft["id"]),expected=404)
    reg.request("/api/v1/content/"+str(draft["id"])+"/actions",{"action":"publish","version":draft["version"]})
    assert anonymous.request("/api/v1/public/articles/"+str(draft["id"]))["body"]==draft["body"]
    reg.request(proof["path"]+"/actions",{"action":"withdraw","version":proof["published"]["version"]})
    anonymous.request(proof["reading"],expected=404)
    for download in proof["files"]:
        anonymous.request(download,expected=404)
    print("Public portal restore: explicit consent, anonymous safe projection, rich text/image/PDF and private/pending boundaries preserved; pending public draft published explicitly, withdrawal revokes anonymous article and both files; authenticated-all remains private.",flush=True)


def main():
    key = secrets.token_hex(5)
    source_env = ROOT / f"deploy/.env.verify-{key}"
    target_env = ROOT / f"deploy/.env.restore-{key}"
    initialize(source_env, "ronghub-verify-" + key, available_port())
    initialize(target_env, "ronghub-restore-" + key, available_port())
    source, target = Stack(source_env), Stack(target_env)
    backup = ROOT / ".backups" / ("verify-" + key)
    try:
        source.up()
        source.seed()
        print("Isolated source stack started and seeded.", flush=True)
        origin = source.values["PUBLIC_ORIGIN"]
        clients = [Client(origin, source) for _ in range(4)]
        reg, owner, other, association = clients
        for client, name in zip(clients, ["regulator", "operator_a", "operator_b", "association"]):
            client.login(name, source.values["DEMO_PASSWORD"])
        own = owner.request("/api/v1/stations")["items"]
        others = other.request("/api/v1/stations")["items"]
        assert len(own) == 2 and len(others) == 1
        assert not {s["id"] for s in own}.intersection(s["id"] for s in others)
        station = next(s for s in own if s["status"] == "pending")
        application = owner.request(
            "/api/v1/applications",
            {
                "kind": "access",
                "station_id": station["id"],
                "payload": {"note": "Docker recovery fixture"},
            },
            expected=201,
        )
        application_path = f"/api/v1/applications/{application['id']}"
        application = owner.request(application_path, {"version": application["version"], "note": "Edited source application proof"}, method="PATCH")
        payload = b"%PDF-1.4\nDocker coordinated recovery proof.\n"
        raw, content_type = multipart(payload)
        attachment = owner.request(
            f"/api/v1/applications/{application['id']}/attachments",
            raw=raw,
            content_type=content_type,
            expected=201,
        )
        download = f"/api/v1/attachments/{attachment['id']}/download"
        assert owner.request(download) == payload
        other.request(download, expected=404)
        association.request(download, expected=403)
        category = owner.request(application_path+f"/attachments/{attachment['id']}/category", {"version": application["version"], "revision": attachment["revision"], "category": "other"}, method="PATCH")
        assert category["material"]["category"] == "other"
        review = owner.request(application_path+"/review-context", private=True)
        application = owner.request(
            application_path+"/actions", {"action": "submit", "version": application["version"], "review_token": review["review_token"]}
        )
        submissions_path = application_path+"/submissions"
        submitted_proof = owner.request(submissions_path, private=True)["items"][0]
        submission_path = submissions_path+f"/{submitted_proof['id']}"
        frozen_proof = owner.request(submission_path, private=True)
        assert frozen_proof["source_reviewed"] and frozen_proof["content"]["materials"][0]["category"] == "other"
        frozen_download = submission_path+f"/materials/{attachment['id']}/download"
        assert owner.request(frozen_download, private=True) == payload
        frozen_archive_path = submission_path+"/archive"
        frozen_archive_digest = hashlib.sha256(owner.request(frozen_archive_path, expected_media="application/zip", private=True)).hexdigest()
        other.request(submission_path, expected=404)
        association.request(submission_path, expected=403)
        application = reg.request(
            application_path+"/actions",
            {"action": "approve", "version": application["version"], "comment": "Docker demo approval"},
        )
        standard = owner.request("/api/v1/ratings/standards")["items"][0]
        rating = owner.request(
            "/api/v1/ratings/applications",
            {
                "station_id": station["id"],
                "standard_id": standard["id"],
                "scores": {"service": "90", "safety": "80"},
                "evidence_note": "Docker large evidence",
            },
            expected=201,
        )
        archive_stream = io.BytesIO()
        with zipfile.ZipFile(archive_stream, "w", compression=zipfile.ZIP_STORED) as zipped:
            zipped.writestr("fixture.bin", b"0" * (11 * 1024 * 1024))
        archive = archive_stream.getvalue()
        raw, content_type = multipart(
            archive, "large-proof.zip", "application/zip", {"category": "site_plan"}
        )
        large = owner.request(
            f"/api/v1/ratings/applications/{rating['id']}/attachments",
            raw=raw,
            content_type=content_type,
            expected=201,
        )
        large_download = (
            f"/api/v1/ratings/applications/{rating['id']}/attachments/{large['id']}/download"
        )
        material_archive_path = f"/api/v1/ratings/applications/{rating['id']}/attachments/archive"
        material_archive_before = owner.request(material_archive_path)
        with zipfile.ZipFile(io.BytesIO(material_archive_before)) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["application_id"] == rating["id"]
            assert manifest["application_version"] == 1
            assert len(manifest["materials"]) == 1
            material = manifest["materials"][0]
            assert material["submitted_version"] is None
            assert hashlib.sha256(bundle.read(material["entry"])).hexdigest() == large["sha256"]
            assert bundle.read(material["entry"]) == archive
        other.request(material_archive_path, expected=404)
        assert association.request(material_archive_path) == material_archive_before
        owner_info = owner.request("/api/v1/auth/me")
        sync_body = {"operator_id": owner_info["operator_id"], "request_key": "verify-" + key}
        choices = reg.request("/api/v1/sync/operators")["items"]
        assert any(item["id"] == owner_info["operator_id"] for item in choices)
        assert all(set(item) == {"id", "name"} for item in choices)
        owner.request("/api/v1/sync/operators", expected=403)
        task = reg.request(
            "/api/v1/sync/tasks",
            sync_body,
            expected=202,
        )
        assert reg.request("/api/v1/sync/tasks", sync_body, expected=202)["id"] == task["id"]
        sync_lookup_path = "/api/v1/sync/tasks?" + urllib.parse.urlencode({"request_key": sync_body["request_key"]})
        deadline = time.monotonic() + 25
        while True:
            status = next(
                t["status"]
                for t in reg.request("/api/v1/sync/tasks")["items"]
                if t["id"] == task["id"]
            )
            if status == "succeeded":
                break
            if time.monotonic() > deadline:
                raise AssertionError("Worker did not finish fixture task")
            time.sleep(0.4)
        sync_before = reg.request(sync_lookup_path)
        assert [item["id"] for item in sync_before["items"]] == [task["id"]]
        assert "lease_owner" not in sync_before["items"][0]
        owner.request("/api/v1/map")
        synced = owner.request("/api/v1/stations?" + urllib.parse.urlencode({"q":"演示·接口同步场站"}))["items"]
        assert len(synced) == 1
        device_days_path = "/api/v1/subsidy/device-days?" + urllib.parse.urlencode({"station_id":synced[0]["id"],"source":"demo","equipment_external_id":"equipment-1","start_date":"2026-11-01","end_date":"2026-11-03"})
        device_days_before=owner.request(device_days_path)
        assert device_days_before["total"]==3
        device_day_rows={row["day"]:row for row in device_days_before["items"]}
        assert device_day_rows["2026-11-02"]["energy_kwh"]=="0.000000" and device_day_rows["2026-11-02"]["fault_percent"]=="0.000000"
        assert device_day_rows["2026-11-03"]["energy_kwh"] is None and device_day_rows["2026-11-03"]["fault_percent_reason"]=="partial"
        assert all(not row["settlement_verified"] for row in device_days_before["items"])
        assert other.request(device_days_path)["total"]==0
        association.request(device_days_path,expected=403)
        device_days_csv_path=device_days_path.replace("/device-days?","/device-days/export?")
        device_days_csv_before=owner.request(device_days_csv_path)
        runtime_path = "/api/v1/statistics/runtime?" + urllib.parse.urlencode({"station_id":synced[0]["id"],"source":"demo","equipment_external_id":"equipment-1","connector_external_id":"connector-1","start_at":"2026-11-01T08:00:00+08:00","end_at":"2026-11-01T12:00:00+08:00"})
        runtime_before = owner.request(runtime_path)
        assert runtime_before["total"] == 8 and runtime_before["source_completeness_verified"] is False
        assert runtime_before["streams"][0]["axes"][0]["unknown_seconds"] == "1800.000000"
        role_check = """
from pathlib import Path
import os
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.db import SessionLocal
from app.models import RuntimeInterval
assert os.getuid() != 0 and not Path('/app/.env').exists()
with SessionLocal() as db:
    assert db.scalar(text('SELECT current_user')) == 'ronghub_app'
    assert db.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')")) is False
    assert db.scalar(text("SELECT has_table_privilege(current_user,'alembic_version','UPDATE')")) is False
    assert db.scalar(text("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='btree_gist'")) == 'public'
    row = db.scalar(select(RuntimeInterval).where(RuntimeInterval.source == 'demo').order_by(RuntimeInterval.id))
    assert row is not None
    values = {column.name:getattr(row,column.name) for column in RuntimeInterval.__table__.columns if column.name != 'id'}
    values['external_id'] = 'restore-overlap-proof'
    try:
        with db.begin_nested():
            db.add(RuntimeInterval(**values))
            db.flush()
    except IntegrityError as error:
        assert error.orig.diag.constraint_name == 'ex_runtime_no_overlap'
    else:
        raise AssertionError('Restored database accepted overlapping runtime intervals')
print('Runtime SQL privileges, non-root image, shared extension and actual exclusion checks passed.')
"""
        source.run("exec", "-T", "api", "python", "-c", role_check)
        rich = reg.request("/api/v1/content", {"kind":"announcement","title":"Docker rich content " + key,"body":"公告正文","rich_document":{"type":"doc","content":[{"type":"heading","attrs":{"level":2},"content":[{"type":"text","text":"恢复验证公告"}]}]}},expected=201)
        content_path = f"/api/v1/content/{rich['id']}"
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfFoAAAAASUVORK5CYII=")
        raw,content_type = multipart(png,"article-proof.png","image/png",{"version":"1"})
        article_assets = reg.request(content_path+"/attachments",raw=raw,content_type=content_type,expected=201)["attachments"]
        raw,content_type = multipart(payload,"article-proof.pdf","application/pdf",{"version":"2"})
        article_assets = reg.request(content_path+"/attachments",raw=raw,content_type=content_type,expected=201)["attachments"]
        reg.request(content_path,{"kind":"announcement","title":"Docker rich content " + key,"body":"ignored cache","rich_document":{"type":"doc","content":[{"type":"heading","attrs":{"level":2},"content":[{"type":"text","text":"恢复验证公告"}]},{"type":"privateImage","attrs":{"attachment_id":article_assets[0]["id"],"alt":"恢复验证图片"}}]},"version":3},method="PATCH")
        reg.request(content_path+"/actions",{"action":"publish","version":4})
        content_before = owner.request(content_path)
        assert content_before["rich_document"] is not None and all(asset["frozen"] for asset in content_before["attachments"])
        content_downloads = [(content_path+f"/attachments/{asset['id']}",asset["sha256"]) for asset in content_before["attachments"]]
        for path,expected_hash in content_downloads:
            assert hashlib.sha256(association.request(path)).hexdigest() == expected_hash
        notice_ids = [row["id"] for row in reg.request("/api/v1/content/operator-options")["items"]]
        assert owner_info["operator_id"] in notice_ids and len(notice_ids) >= 2
        notice = reg.request("/api/v1/content", {"kind":"notice", "title":"Docker multi notice " + key, "body":"独立收件恢复验证", "audience":"operator", "recipient_ids":notice_ids}, expected=201)
        notice_path = f"/api/v1/content/{notice['id']}"
        reg.request("/api/v1/content/batch-actions", {"action":"publish", "items":[{"id":notice["id"],"version":1}]})
        deleted_notice = reg.request("/api/v1/content", {"kind":"notice", "title":"Docker deleted notice " + key, "body":"保留删除草稿历史", "audience":"operator", "recipient_ids":notice_ids}, expected=201)
        deleted_notice_path = f"/api/v1/content/{deleted_notice['id']}"
        reg.request("/api/v1/content/batch-actions", {"action":"delete", "items":[{"id":deleted_notice["id"],"version":1}]})
        deleted_notice_before = reg.request(deleted_notice_path+"?manage=true")
        assert deleted_notice_before["status"] == "deleted"
        reply_raw, reply_type = multipart(png, "reply-proof.png", "image/png",
                                          {"body":"恢复前回复", "contact_name":"测试联系人", "contact_phone":"13800000000", "rich_document":json.dumps({"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"恢复前回复","marks":[{"type":"bold"}]}]},{"type":"privateImage","attrs":{"attachment_id":1,"alt":"恢复验证回复图片"}}]}, ensure_ascii=False)}, file_field="files")
        reply_result = owner.request(notice_path+"/replies/with-images", raw=reply_raw, content_type=reply_type, expected=201, private=True)
        notice_csv_before = reg.request("/api/v1/content/notice-records/export?article_id="+str(notice["id"]))
        notice_before = reg.request(notice_path+"?manage=true")
        owner_notice_before = owner.request(notice_path)
        reply_image = next(item for item in owner_notice_before["replies"] if item["id"] == reply_result["id"])["images"][0]
        reply_document_before = next(item for item in owner_notice_before["replies"] if item["id"] == reply_result["id"])["rich_document"]
        assert reply_document_before["content"][0]["content"][0]["marks"][0]["type"] == "bold"
        assert reply_document_before["content"][1]["attrs"]["attachment_id"] == reply_image["id"]
        notice_image_path = notice_path + f"/replies/{reply_result['id']}/images/{reply_image['id']}"
        assert hashlib.sha256(owner.request(notice_image_path, expected_media="image/png", private=True)).hexdigest() == reply_image["sha256"]
        other.request(notice_image_path, expected=404)
        assert len(notice_before["deliveries"]) == len(notice_ids)
        assert len(owner_notice_before["deliveries"]) == 1
        enterprise_applicant=Client(origin,source)
        registration_username="restore-enrollment-"+key
        registration_password=secrets.token_urlsafe(24)
        registration_body={"username":registration_username,"password":registration_password,
            "company_name":"Synthetic enterprise enrollment "+key,"credit_code":"A"+secrets.token_hex(9).upper()[:17],
            "registration_address":"Synthetic pending registration","contacts":[{"name":"Synthetic applicant","phone":"100006","primary":True}]}
        enterprise_created=enterprise_applicant.request("/api/v1/enrollment",registration_body,expected=201)
        enterprise_applicant.enrollment_csrf=enterprise_created["csrf_token"]
        enterprise_id=enterprise_created["id"]
        enterprise_path=f"/api/v1/registrations/{enterprise_id}"
        assert enterprise_created["operator_id"] is None and enterprise_created["user_id"] is None
        enterprise_applicant.request("/api/v1/auth/me",expected=401)
        material_bytes=b"%PDF-1.4\nSynthetic enterprise enrollment restore proof"
        enterprise_raw,enterprise_type=multipart(material_bytes,"enterprise-proof.pdf","application/pdf")
        enterprise_material=enterprise_applicant.request("/api/v1/enrollment/me/attachments",raw=enterprise_raw,content_type=enterprise_type,expected=201)
        enterprise_submitted=enterprise_applicant.request("/api/v1/enrollment/me/submit",{"version":1})
        registration_role=reg.request("/api/v1/roles",{"name":"Restore enterprise role "+key,"base_role":"operator","permissions":["stations.read"]},expected=201)
        rejected_registration=reg.request(enterprise_path+"/review",{"version":enterprise_submitted["version"],"action":"reject","comment":"Synthetic correction round"})
        enterprise_edited=enterprise_applicant.request("/api/v1/enrollment/me",{**{k:v for k,v in registration_body.items() if k not in {"username","password"}},"version":rejected_registration["version"],"registration_address":"Synthetic corrected registration"},method="PATCH")
        enterprise_applicant.request("/api/v1/enrollment/me/submit",{"version":enterprise_edited["version"]})
        enterprise_before=reg.request(enterprise_path)
        enterprise_materials_before=enterprise_applicant.request("/api/v1/enrollment/me/attachments")
        enterprise_progress_before={k:v for k,v in enterprise_applicant.request("/api/v1/enrollment/me").items() if k!="csrf_token"}
        enterprise_file_path=enterprise_path+f"/attachments/{enterprise_material['id']}/download"
        enterprise_file_hash=hashlib.sha256(reg.request(enterprise_file_path)).hexdigest()
        assert enterprise_file_hash==enterprise_material["sha256"]
        phone_issue = owner.request("/api/v1/auth/phone/challenges", {"phone":"13900000001", "current_password":source.values["DEMO_PASSWORD"]}, expected=201)
        assert phone_issue["delivery_mode"] == "demo" and "code" not in phone_issue
        phone_seed = json.loads(source.run("exec","-T","api","python","-m","app.sms_fixture","--container-preview","--",phone_issue["challenge_id"],capture=True).stdout)
        owner.request("/api/v1/auth/phone/confirm", {"challenge_id":phone_issue["challenge_id"],"code":phone_seed["code"]})
        owner.request("/api/v1/auth/me", expected=401)
        owner.login("operator_a",source.values["DEMO_PASSWORD"])
        sms_device=Client(origin,source)
        sms_login,sms_code=sms_device.sms_challenge("13900000001","login",kind="phone")
        sms_device.request("/api/v1/auth/sms/login",{"challenge_id":sms_login["challenge_id"],"code":sms_code})
        assert sms_device.request("/api/v1/auth/me")["username"]=="operator_a"
        recovery_device=Client(origin,source)
        recovery,sms_code=recovery_device.sms_challenge("operator_a","recovery")
        owner_password=secrets.token_urlsafe(24)
        recovery_device.request("/api/v1/auth/sms/recover",{"challenge_id":recovery["challenge_id"],"code":sms_code,"new_password":owner_password})
        owner.request("/api/v1/auth/me",expected=401)
        sms_device.request("/api/v1/auth/me",expected=401)
        owner.login("operator_a",owner_password)
        # Real authenticated two-phone flow, using only the isolated demo SMS fixture.
        def owner_sms_code(token):
            return json.loads(source.run("exec","-T","api","python","-m","app.sms_fixture","--container-preview","--",token,capture=True).stdout)["code"]
        before_change = owner.request("/api/v1/auth/phone")
        old_change = owner.request("/api/v1/auth/phone/change/challenges", {"current_password":owner_password,"phone_version":before_change["version"]},expected=201)
        new_change = owner.request("/api/v1/auth/phone/change/new-challenges", {"old_challenge_id":old_change["challenge_id"],"old_code":owner_sms_code(old_change["challenge_id"]),"phone":"13800000001"},expected=201)
        assert owner.request("/api/v1/auth/phone")==before_change
        changed_phone = owner.request("/api/v1/auth/phone/change/confirm", {"challenge_id":new_change["challenge_id"],"code":owner_sms_code(new_change["challenge_id"])})
        assert changed_phone["phone_masked"]=="138****0001"
        owner.request("/api/v1/auth/me",expected=401)
        owner.login("operator_a",owner_password)
        phone_before = owner.request("/api/v1/auth/phone")
        assert phone_before["verified"] and phone_before["verification_mode"] == "demo"
        assert phone_before["version"]==before_change["version"]+1
        pending_old_change=owner.request("/api/v1/auth/phone/change/challenges",{"current_password":owner_password,"phone_version":phone_before["version"]},expected=201)
        pending_new_change=owner.request("/api/v1/auth/phone/change/new-challenges",{"old_challenge_id":pending_old_change["challenge_id"],"old_code":owner_sms_code(pending_old_change["challenge_id"]),"phone":"13800000004"},expected=201)
        pending_new_code=owner_sms_code(pending_new_change["challenge_id"])

        other.request("/api/v1/auth/phone/challenges", {"phone":"13900000002", "current_password":source.values["DEMO_PASSWORD"]}, expected=201)
        applicant=Client(origin,source)
        staff_request=applicant.staff_request("association","Docker staff " + key,"13900000003")
        applicant.request("/api/v1/auth/me",expected=401)
        profile=reg.request("/api/v1/roles",{"name":"Docker staff role "+key,"base_role":"association","permissions":["stations.read","content.read"]},expected=201)
        staff_username="verify-staff-"+key
        staff_password=secrets.token_urlsafe(24)
        staff_path=f"/api/v1/staff-applications/{staff_request['id']}"
        reg.request(staff_path+"/review",{"action":"approve","version":1,"comment":"演示人工核验，凭据另行交付","account":{"username":staff_username,"temporary_password":staff_password,"role_profile_id":profile["id"],"role_profile_version":profile["version"],"allowed_districts":["鼓楼区"]}})
        staff_before=reg.request(staff_path)
        staff_status=applicant.request("/api/v1/auth/staff-enrollment/me")
        assert staff_status["username"]==staff_username and "grant_snapshot" not in staff_status
        staff_account=Client(origin,source)
        staff_account.login(staff_username,staff_password)
        assert staff_account.request("/api/v1/auth/me")["must_change_password"]
        staff_account.request("/api/v1/stations",expected=403)
        configured_alert_code = "RESTORE-ALERT-"+key
        alert_body = {"code":configured_alert_code,"version":1,"name":"Demo multi-level restore","notifications_enabled":True,"configuration":{"configuration_version":1,"kind":"device_temperature","direction":"gte","period":"instant","thresholds":{"general":"60","severe":"70","critical":"80"},"recover_value":"50"}}
        configured_alerts = [reg.request("/api/v1/alerts/configured-rules",alert_body,expected=201)]
        alert_body = json.loads(json.dumps(alert_body))
        alert_body["version"] = 2
        alert_body["configuration"].update(kind="station_fault",period="natural_month",thresholds={"general":"10","critical":"20"},recover_value="5")
        second_alert = reg.request("/api/v1/alerts/configured-rules",alert_body,expected=201)
        configured_alerts.append(reg.request(f"/api/v1/alerts/configured-rules/{second_alert['id']}/availability",{"revision":1,"enabled":False},method="PATCH"))
        alert_body["version"] = 3
        third_alert = reg.request("/api/v1/alerts/configured-rules",alert_body,expected=201)
        configured_alerts.append(reg.request(f"/api/v1/alerts/configured-rules/{third_alert['id']}?revision=1",method="DELETE"))
        alert_configuration_path = "/api/v1/alerts/configured-rules?"+urllib.parse.urlencode({"q":configured_alert_code,"include_archived":"true"})
        alert_configuration_before = reg.request(alert_configuration_path)
        assert alert_configuration_before["total"]==3
        association.request("/api/v1/alerts/configured-rules",expected=403)
        operating = next(station for station in own if station["status"] == "operating")
        inspection_requirement = {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Synthetic major hazard recovery workflow","marks":[{"type":"bold"}]}]}]}
        inspection_performed_at=(datetime.now(ZoneInfo("UTC"))-timedelta(days=1)).isoformat()
        inspection_task=reg.request("/api/v1/inspections",{"station_id":operating["id"],"title":"Synthetic inspection "+key,"description":"Synthetic major hazard recovery workflow","rich_document":inspection_requirement,"severity":"major","due_at":(datetime.now(ZoneInfo("UTC"))+timedelta(days=1)).isoformat()},expected=201)
        inspection_path=f"/api/v1/inspections/{inspection_task['id']}"
        inspection_materials=[]
        for index in [1,2]:
            raw_proof,proof_type=multipart(png if index==1 else b"%PDF-1.4\nSynthetic inspection evidence "+str(index).encode(),f"inspection-{index}.png" if index==1 else f"inspection-{index}.pdf","image/png" if index==1 else "application/pdf")
            inspection_materials.append(owner.request(inspection_path+"/attachments",raw=raw_proof,content_type=proof_type,expected=201))
            if index==1:
                reply=owner.request(inspection_path+"/actions",{"version":inspection_task["version"],"action":"reply","comment":"Synthetic first correction","rich_document":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Synthetic first correction","marks":[{"type":"bold"}]}]},{"type":"privateImage","attrs":{"attachment_id":inspection_materials[-1]["id"],"alt":"Inspection evidence"}}]},"contact":{"name":"Synthetic executor","phone":"13800000001"},"performed_at":inspection_performed_at})
                inspection_task=reg.request(inspection_path+"/actions",{"version":reply["version"],"action":"return","comment":"Synthetic request for extra evidence"})
        inspection_before=reg.request(inspection_path)
        inspection_materials_before=owner.request(inspection_path+"/attachments")
        assert inspection_before["rich_document"] is not None
        frozen_execution=inspection_before["history"][1]
        assert frozen_execution["rich_document"]["content"][1]["attrs"]["attachment_id"]==inspection_materials[0]["id"]
        assert frozen_execution["contact"]=={"name":"Synthetic executor","phone":"13800000001"}
        assert datetime.fromisoformat(frozen_execution["performed_at"])==datetime.fromisoformat(inspection_performed_at)

        assert inspection_before["status"]=="returned" and len(inspection_before["history"])==3
        assert inspection_materials_before["items"][0]["action_id"] is not None and inspection_materials_before["items"][1]["action_id"] is None
        inspection_files_before={inspection_path+f"/attachments/{material['id']}/download":material["sha256"] for material in inspection_materials}
        for path,expected_digest in inspection_files_before.items():
            assert hashlib.sha256(owner.request(path)).hexdigest()==expected_digest
            other.request(path,expected=404)
        association.request(inspection_path,expected=403)
        public_portal_proof=prepare_public_portal(reg,content_before["id"],png,key)
        protocol_proof=prepare_protocol_proof(reg,owner,association,key)
        hazard_proof=prepare_hazard_proof(reg,owner,other,association,operating,key)
        inspection_query_proof=prepare_inspection_query_proof(owner,other,association,inspection_before)
        inspection_plan_proof=prepare_inspection_plans(reg,owner,association,[operating["id"],others[0]["id"]],key)


        policy = owner.request("/api/v1/subsidy/policies")["items"][0]
        assert policy["device_rules"] is None and policy["claim_supported"]
        device_body = {"code":"DEVICE-RESTORE-"+key,"version":1,"name":"Device restore rules","rate":"0.100001","effective_from":"2026-01-01","effective_to":"2026-12-31","device_rules":{"configuration_version":1,"swap_rate":"0.200001","generation_period":"day","star_coefficients":{str(star):"1.200001" for star in range(6)},"fault_rate_below":"10.2500","offline_rate_below":"20.0000"}}
        device_policies_before = [reg.request("/api/v1/subsidy/policies",device_body,expected=201)]
        device_body["version"]=2
        device_body["device_rules"]["star_coefficients"]["5"]="1.500001"
        device_body["device_rules"]["generation_period"]="month"
        device_policies_before.append(reg.request("/api/v1/subsidy/policies",device_body,expected=201))
        device_policy_ids={row["id"] for row in device_policies_before}
        blocked_device_claim={"station_id":operating["id"],"policy_id":device_policies_before[0]["id"],"start_date":"2026-11-01","end_date":"2026-11-02"}
        owner.request("/api/v1/subsidy/claims",blocked_device_claim,expected=409)
        # Explicit synthetic history in this randomly owned demo stack; all financial writes use HTTP.
        device_seed = """
import secrets
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import Station,User
assert settings.demo_mode
with SessionLocal.begin() as db:
    owner=db.scalar(select(User).where(User.username=='operator_a'))
    key=secrets.token_hex(12)
    station=Station(operator_id=owner.operator_id,source='browser-test',external_id=key,name='浏览器验收-restore-'+key,district='鼓楼区',address='Synthetic restore fixture',power_kw=120,connectors=1,status='operating')
    db.add(station);db.flush();sid=station.id
print(sid)
"""
        executable_station = int(source.run("exec","-T","api","python","-c",device_seed,capture=True).stdout)
        source.run("exec","-T","api","python","-m","app.device_execution_fixture","--container-preview","--station",str(executable_station),capture=True)
        executable_body = json.loads(json.dumps(device_body))
        executable_body.update(code="EXEC-RESTORE-"+key,version=1)
        executable_body["device_rules"].update(configuration_version=2,metric_basis="calendar_day_seconds_v1",device_scope_basis="source_declared_station_day_v1",rating_reference="period_end_publication_snapshot_v1",rounding="HALF_UP_PER_DEVICE_DAY_THEN_COEFFICIENT_2DP")
        executable_policy = reg.request("/api/v1/subsidy/policies",executable_body,expected=201)
        executable_payload = {"station_id":executable_station,"policy_id":executable_policy["id"],"start_date":"2026-11-01","end_date":"2026-11-02"}
        executable_claim = owner.request("/api/v1/subsidy/claims",executable_payload,expected=201)
        executable_path = f"/api/v1/subsidy/claims/{executable_claim['id']}"
        executable_claim = owner.prepare_subsidy(executable_claim)
        owner.request(executable_path+"/actions",{"action":"submit","version":executable_claim["version"],"comment":"Synthetic device restore proof"})
        executable_before = owner.request(executable_path)
        executable_lines = owner.request(executable_path+"/device-lines")
        subsidy_statistics_before = {}
        for dimension in ["operator", "station", "equipment", "device_day"]:
            query = urllib.parse.urlencode({"start_date":"2026-11-01","end_date":"2026-11-02","station_id":executable_station,"status":"submitted","dimension":dimension})
            statistics_path = "/api/v1/subsidy/statistics?"+query
            subsidy_statistics_before[statistics_path] = owner.request(statistics_path)
            export_path = "/api/v1/subsidy/statistics/export?"+query
            subsidy_statistics_before[export_path] = owner.request(export_path)
            assert subsidy_statistics_before[statistics_path]["totals"]["claim_count"] == 1

        assert executable_before["base_amount"]=="4.21" and executable_before["adjusted_amount"]=="6.32"
        assert executable_lines["total"]==2
        owner.request("/api/v1/subsidy/claims",executable_payload,expected=409)
        # Seed two clearly isolated demo stations; all annual business writes still use HTTP.
        annual_seed = """
import json,secrets
from datetime import date
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import User,Station,Equipment,EnergyRecord
assert settings.demo_mode
ids=[]
with SessionLocal.begin() as db:
    user=db.scalar(select(User).where(User.username=='operator_a'))
    for _ in range(2):
        key=secrets.token_hex(12)
        station=Station(operator_id=user.operator_id,source='restore-annual-fixture',external_id=key,name='Demo annual restore '+key,district='鼓楼区',address='Isolated restore fixture',power_kw=60,connectors=1,status='operating')
        db.add(station); db.flush()
        db.add(Equipment(station_id=station.id,external_id=key,name='Demo terminal',power_kw=60))
        db.add(EnergyRecord(station_id=station.id,source='restore-annual-fixture',external_id=key,day=date(2026,1,1),kwh='42.005',eligible=True))
        ids.append(station.id)
print(json.dumps(ids))
"""
        annual_station_ids = json.loads(source.run("exec", "-T", "api", "python", "-c", annual_seed, capture=True).stdout)
        observation_station = annual_station_ids[0]
        legacy_rule = reg.request("/api/v1/alerts/rules", {
            "code": "RESTORE-LEGACY-" + key, "version": 1, "name": "Synthetic legacy restore",
            "metric": "offline_minutes", "unit": "分钟", "trigger_value": "30", "recover_value": "5",
        }, expected=201)
        legacy_sample = {"station_id": observation_station, "rule_id": legacy_rule["id"],
                         "target": "restore-legacy", "value": "40", "observed_at": "2026-11-01T08:00:00+08:00"}
        legacy_first = reg.request("/api/v1/alerts/samples", legacy_sample)
        assert legacy_first["outcome"] == "opened"
        legacy_event_path = "/api/v1/alerts/" + str(legacy_first["event_id"])
        legacy_event_before = reg.request(legacy_event_path)
        legacy_rule_before = next(item for item in reg.request("/api/v1/alerts/rules")["items"] if item["id"] == legacy_rule["id"])
        observation_equipment = reg.request(f"/api/v1/alerts/configured-observations/equipment?station_id={observation_station}")["items"][0]["id"]
        # Explicit synthetic period replay in this random verifier stack only; no HTTP history bypass.
        period_rule = reg.request("/api/v1/alerts/configured-rules", {
            "code":"RESTORE-PERIOD-"+key,"version":1,"name":"Synthetic automatic period restore",
            "configuration":{"configuration_version":1,"kind":"station_fault","direction":"gte",
                             "period":"natural_week","thresholds":{"general":"5"},"recover_value":"4"},
            "notifications_enabled":True},expected=201)
        period_seed = """
import sys
from datetime import datetime, timezone
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import AlertRule, Station, Equipment
from app.alert_period_facts import ingest
assert settings.demo_mode
with SessionLocal.begin() as db:
    station=db.scalar(select(Station).where(Station.id==int(sys.argv[1])).with_for_update())
    rule=db.get(AlertRule,int(sys.argv[2]))
    assert station and station.source=='restore-annual-fixture' and station.name.startswith('Demo annual restore ')
    assert rule and rule.code==sys.argv[3] and rule.code.startswith('RESTORE-PERIOD-') and rule.is_demo
    equipment=db.scalar(select(Equipment).where(Equipment.station_id==station.id))
    rule.monitor_from=datetime(2026,10,1,tzinfo=timezone.utc)
    ingest(db,station_id=station.id,operator_id=station.operator_id,source=station.source,records=[{
        'external_id':'synthetic-period-restore','metric':'station_fault','period':'natural_week',
        'period_start':'2026-10-05','period_end':'2026-10-12','basis':'source_declared_equipment_service_seconds_v1',
        'equipment_external_ids':[equipment.external_id],'members_complete':True,
        'members':[{'equipment_external_id':equipment.external_id,'equipment_name':'Synthetic historical cohort',
                    'expected_seconds':'604800','observed_seconds':'604800','unknown_seconds':'0','affected_seconds':'60480'}],
        'source_updated_at':'2026-10-12T00:00:00+08:00'}])
print('Seeded one synthetic closed period and explicit fixture monitor start.')
"""
        source.run("exec","-T","api","python","-c",period_seed,str(observation_station),str(period_rule["id"]),period_rule["code"])
        period_receipts_path=f"/api/v1/alerts/configured-observations?station_id={observation_station}&rule_id={period_rule['id']}"
        deadline=time.monotonic()+30
        while True:
            period_receipts_before=reg.request(period_receipts_path)
            if period_receipts_before["total"]==1: break
            if time.monotonic()>deadline: raise AssertionError("Worker did not consume synthetic closed period")
            time.sleep(.25)
        period_observation=period_receipts_before["items"][0]
        assert period_observation["created_by"] is None and period_observation["result"]["outcome"]=="opened"
        assert period_observation["evidence"]["source_basis"]=="adapter_period_demo_v1"
        assert period_observation["evidence"]["source_period"]["fact_version"]==1
        period_facts_path=f"/api/v1/alerts/period-facts?station_id={observation_station}"
        period_facts_before=reg.request(period_facts_path)
        assert period_facts_before["items"][0]["percent"]=="10.000000"
        period_event_path=f"/api/v1/alerts/{period_observation['result']['event_id']}"
        period_event_before=reg.request(period_event_path)
        independent_body = {"request_key":str(uuid4()),"station_id":observation_station,"equipment_id":observation_equipment,"rule_id":configured_alerts[0]["id"],"value":"60","coverage_complete":True,"observed_at":"2026-11-01T08:00:00+08:00"}
        notification_preference_before=reg.request("/api/v1/alerts/notifications/preferences/device_charging",{"version":0,"in_app_enabled":False,"sms_enabled":True},method="PUT")
        notification_recipient=reg.request("/api/v1/alerts/notifications/recipients",{"name":"Demo restore recipient","phone":"18800000009","categories":["device_charging"],"enabled":True},expected=201)
        independent_first = reg.request("/api/v1/alerts/configured-observations",independent_body,expected=201)
        assert independent_first["outcome"]=="opened"
        assert reg.request("/api/v1/alerts/configured-observations",independent_body,expected=201)==independent_first
        notification_recipient=reg.request(f"/api/v1/alerts/notifications/recipients/{notification_recipient['id']}",{"version":1,"name":"Demo restore recipient revised","phone":"18800000008","categories":["device_charging"],"enabled":True},method="PATCH")

        for value,complete,stamp in [("80",True,"2026-11-01T08:01:00+08:00"),(None,False,"2026-11-01T08:02:00+08:00")]:
            reg.request("/api/v1/alerts/configured-observations",{**independent_body,"request_key":str(uuid4()),"value":value,"coverage_complete":complete,"observed_at":stamp},expected=201)
        independent_path = f"/api/v1/alerts/{independent_first['event_id']}"
        alert_csv_path="/api/v1/alerts/export?"+urllib.parse.urlencode({"record_id":independent_first["event_id"],"category":"device_charging","start_at":"2026-11-01T00:00:00+08:00","end_at":"2026-12-01T00:00:00+08:00"})
        alert_csv_before=reg.request(alert_csv_path)
        assert "事件编号" in alert_csv_before.decode("utf-8-sig") and "短信通知号码" not in alert_csv_before.decode("utf-8-sig")
        assert reg.request("/api/v1/alerts?"+urllib.parse.urlencode({"record_id":independent_first["event_id"],"category":"device_charging"}))["total"]==1
        independent_before = reg.request(independent_path)
        independent_records_path = "/api/v1/alerts/configured-observations?"+urllib.parse.urlencode({"station_id":observation_station,"rule_id":configured_alerts[0]["id"]})
        independent_records_before = reg.request(independent_records_path)
        assert independent_before["status"]=="active" and independent_before["configuration_evidence"]["highest_level"]=="critical"
        assert independent_records_before["total"]==3
        notification_recipient=reg.request(f"/api/v1/alerts/notifications/recipients/{notification_recipient['id']}",{"version":2,"name":"Demo restore recipient revised","phone":"18800000008","categories":["device_charging"],"enabled":False},method="PATCH")
        notification_contacts_path="/api/v1/alerts/notifications/recipients?"+urllib.parse.urlencode({"q":"Demo restore recipient"})
        notification_contacts_before=reg.request(notification_contacts_path)
        notification_routes_path=f"/api/v1/alerts/notifications/routings?event_id={independent_first['event_id']}"
        notification_deliveries_path=f"/api/v1/alerts/notifications/deliveries?event_id={independent_first['event_id']}"
        notification_routes_before=reg.request(notification_routes_path)
        notification_deliveries_before=reg.request(notification_deliveries_path)
        assert notification_routes_before["total"]==2 and notification_deliveries_before["total"]==2
        assert {row["recipient_version"] for row in notification_deliveries_before["items"]}=={1,2}
        assert all(row["status"]=="simulated" for row in notification_deliveries_before["items"])
        assert independent_before["notices"]==[]
        owner.request("/api/v1/alerts/notifications/recipients",expected=403)


        annual_payload = {"request_key": str(uuid4()), "application_year": 2026, "station_ids": annual_station_ids, "policy_id": policy["id"], "application_note": "Demo annual restore evidence", "mode": "submit"}
        annual_zip = io.BytesIO()
        with zipfile.ZipFile(annual_zip,"w") as bundle:
            bundle.writestr(zipfile.ZipInfo("annual-proof.txt",date_time=(1980,1,1,0,0,0)),"Demo annual evidence")
        annual_raw, annual_type = multipart(annual_zip.getvalue(),"annual.zip","application/zip",{"payload":json.dumps(annual_payload)}, file_field="files")
        annual_result = owner.request("/api/v1/subsidy/annual-batches",raw=annual_raw,content_type=annual_type,expected=201)
        assert annual_result["replayed"] is False and len(annual_result["receipt"]["items"])==2
        annual_replayed = owner.request("/api/v1/subsidy/annual-batches",raw=annual_raw,content_type=annual_type,expected=201)
        assert annual_replayed == {"replayed":True,"receipt":annual_result["receipt"]}
        annual_details, annual_downloads = {}, []
        for item in annual_result["receipt"]["items"]:
            annual_path=f"/api/v1/subsidy/claims/{item['id']}"
            annual_detail=owner.request(annual_path)
            assert annual_detail["snapshot"]["application_year"]==2026 and annual_detail["status"]=="submitted"
            annual_details[annual_path]=annual_detail
            material=owner.request(annual_path+"/attachments")["items"][0]
            path=annual_path+f"/attachments/{material['id']}/download"
            assert hashlib.sha256(owner.request(path)).hexdigest()==material["sha256"]
            other.request(path,expected=404)
            annual_downloads.append((path,material["sha256"]))
        configured_body = {
            "code":"RECOVERY-FIELDS-"+key, "version":1, "name":"Demo configured field restore",
            "indicators":[{"enabled":True,"code":"inventory","name":"Observed inventory","weight":"100","max_score":"100","source":"runtime","category":"general","description":"Current database inventory observation; not a formal runtime ratio","effective_from":"2026-01-01","field_name":"equipment_count","bands":[{"lower":"0","upper":"1","score":"0","label":"No observed devices"},{"lower":"1","upper":None,"score":"100","label":"Observed devices"}]}],
            "thresholds":[{"minimum":"90","stars":5}]}
        configured_body["indicators"].append({"enabled":False,"code":"omitted","name":"Disabled rubric","weight":"900","max_score":"100","source":"manual","category":"general","description":"Disabled prospective metric; not a missing or zero score","effective_from":"2099-01-01","field_name":"expert_score","bands":[{"lower":"0","upper":None,"score":"100","label":"Disabled sample band"}]})
        configured_plan = association.request("/api/v1/ratings/standards/preview",configured_body)
        assert configured_plan["excluded"]==["omitted"] and configured_plan["items"][0]["normalization_weight"]=="100"
        assert not any(row["code"]==configured_body["code"] for row in association.request("/api/v1/ratings/standards")["items"])
        configured_standard = association.request("/api/v1/ratings/standards",configured_body,expected=201)
        assert configured_standard["rules"]["indicator_status_version"]==1 and configured_standard["effective_on"]=="2026-01-01"
        assert configured_standard["activation_state"]=="effective" and configured_standard["rules"]["configuration_version"]==2
        configured = owner.request("/api/v1/ratings/applications",{"station_id":annual_station_ids[0],"standard_id":configured_standard["id"],"scores":{},"evidence_note":"Demo frozen current inventory"},expected=201)
        configured_path=f"/api/v1/ratings/applications/{configured['id']}"
        assert configured["calculation"]["total"]=="100.00"
        owner.request(configured_path+"/actions",{"action":"submit","version":1})
        association.request(configured_path+"/actions",{"action":"review","version":2,"comment":"Demo verify frozen inventory","scores":{"inventory":"100"}},expected=422)
        association.request(configured_path+"/actions",{"action":"review","version":2,"comment":"Reject a supplied disabled score","scores":{"omitted":"0"}},expected=422)
        association.request(configured_path+"/actions",{"action":"review","version":2,"comment":"Demo verify frozen inventory","scores":{}})
        association.request(configured_path+"/actions",{"action":"publish","version":3,"comment":"Demo configured publication","valid_until":(datetime.now(ZoneInfo("Asia/Shanghai")).date()+timedelta(days=30)).isoformat()})
        configured_before=owner.request(configured_path)
        assert configured_before["rule_snapshot"]["evaluation_context"]["fields"]["equipment_count"]=="1"
        assert configured_before["calculation"]["details"][0]["raw_value"]=="1"
        assert configured_before["calculation"]["excluded"]==["omitted"] and configured_before["calculation"]["missing"]==[]
        assert configured_before["calculation"]["details"][1]["score"] is None and configured_before["calculation"]["details"][1]["enabled"] is False
        configured_report=owner.request(configured_path+"/report")
        assert "映射档位" in configured_report.decode() and "equipment_count" in configured_report.decode()
        # Configuration lifecycle data must survive the same backup as the frozen rating report.
        standard_draft_before = association.request("/api/v1/ratings/standard-drafts", {"data":{"name":"Demo incomplete recoverable draft"}}, expected=201)
        association.request(f"/api/v1/ratings/standard-drafts/{standard_draft_before['id']}/finalize", {"version":1}, expected=422)
        complete_draft = {"code":"RESTORE-DRAFT-"+key,"name":"Demo standard draft finalization","version":"1","categories":configured_standard["rules"]["categories"] or [],"indicators":configured_standard["rules"]["indicators"],"thresholds":[{"minimum":row["minimum"],"stars":str(row["stars"])} for row in configured_standard["rules"]["thresholds"]]}
        publishing_draft = association.request("/api/v1/ratings/standard-drafts", {"data":complete_draft}, expected=201)
        finalized_draft = association.request(f"/api/v1/ratings/standard-drafts/{publishing_draft['id']}/finalize", {"version":1})
        assert finalized_draft["draft"]["status"]=="published" and finalized_draft["draft"]["version"]==2
        unused_standard_path=f"/api/v1/ratings/standards/{finalized_draft['standard']['id']}"
        association.request(unused_standard_path+"/availability", {"revision":1,"enabled":False})
        archived_standard_before=association.request(unused_standard_path+"?revision=2",method="DELETE")
        assert archived_standard_before["archived"] and archived_standard_before["revision"]==3
        owner.request(unused_standard_path,expected=403)
        deleted_draft = association.request("/api/v1/ratings/standard-drafts", {"data":{"name":"Demo deleted draft"}}, expected=201)
        deleted_draft=association.request(f"/api/v1/ratings/standard-drafts/{deleted_draft['id']}?version=1",method="DELETE")
        draft_details_before={f"/api/v1/ratings/standard-drafts/{row['id']}":association.request(f"/api/v1/ratings/standard-drafts/{row['id']}") for row in [standard_draft_before,finalized_draft["draft"],deleted_draft]}
        assert deleted_draft["status"]=="deleted" and deleted_draft["version"]==2
        owner.request(next(iter(draft_details_before)),expected=403)
        # Disabling a referenced version blocks new claims but does not rewrite published evidence.
        referenced_standard_path=f"/api/v1/ratings/standards/{configured_standard['id']}"
        association.request(referenced_standard_path+"?revision=1",method="DELETE",expected=409)
        disabled_standard_before=association.request(referenced_standard_path+"/availability",{"revision":1,"enabled":False})
        assert disabled_standard_before["revision"]==2 and not disabled_standard_before["enabled"]
        assert owner.request(configured_path)==configured_before
        claim = owner.request("/api/v1/subsidy/claims", {"station_id":operating["id"],"policy_id":policy["id"],"start_date":"2026-11-01","end_date":"2026-11-02"}, expected=201)
        claim_path = f"/api/v1/subsidy/claims/{claim['id']}/actions"
        claim=owner.prepare_subsidy(claim)
        claim=owner.request(claim_path, {"action":"submit","version":claim["version"]})
        claim=reg.request(claim_path, {"action":"adjust","version":claim["version"],"delta":"0.01","comment":"Demo adjustment"})
        reg.request(claim_path, {"action":"approve","version":claim["version"],"comment":"Demo approval"})
        submitted_rating = owner.request(
            "/api/v1/ratings/applications",
            {"station_id": operating["id"], "standard_id": standard["id"], "scores": {"service": "90", "safety": "80"}},
            expected=201,
        )
        submitted_rating_path = f"/api/v1/ratings/applications/{submitted_rating['id']}"
        assert owner.request(submitted_rating_path)["last_submission"] is None
        owner.request(submitted_rating_path + "/actions", {"action": "submit", "version": 1})
        submitted_rating_before = owner.request(submitted_rating_path)
        assert submitted_rating_before["station_name"] == operating["name"]
        assert submitted_rating_before["last_submission"]["current_account_username"] == "operator_a"
        assert submitted_rating_before["last_submission"]["submitted_at"]
        assert reg.request(submitted_rating_path) == submitted_rating_before
        assert association.request(submitted_rating_path) == submitted_rating_before
        other.request(submitted_rating_path, expected=404)
        source.run("exec", "-T", "api", "python", "-c", """
import sys
from datetime import date
from app.config import settings
from app.db import SessionLocal
from app.models import EnergyRecord, Station
assert settings.demo_mode
with SessionLocal.begin() as db:
    station = db.get(Station, int(sys.argv[1]))
    assert station and station.source == 'demo' and station.status == 'operating'
    for index in [1, 2]:
        db.add(EnergyRecord(station_id=station.id, source='verify-batch', external_id=sys.argv[2]+'-'+str(index), day=date(2026, 12, index), kwh='10.05', eligible=True))
""", str(operating["id"]), key)
        batch_claims = []
        for day in ["2026-12-01", "2026-12-02"]:
            pending = owner.request("/api/v1/subsidy/claims", {"station_id": operating["id"], "policy_id": policy["id"], "start_date": day, "end_date": day}, expected=201)
            pending=owner.prepare_subsidy(pending)
            pending=owner.request(f"/api/v1/subsidy/claims/{pending['id']}/actions", {"action": "submit", "version": pending["version"]})
            batch_claims.append(pending)
        batch_body = {"action": "approve", "comment": "Verified demo batch review", "items": [{"id": row["id"], "version": row["version"]} for row in reversed(batch_claims)]}
        batch_result = reg.request("/api/v1/subsidy/claims/batch-actions", batch_body)
        assert batch_result["total"] == 2 and all(row["status"] == "approved" and row["version"] == 5 for row in batch_result["items"])
        reg.request("/api/v1/subsidy/claims/batch-actions", batch_body, expected=409)
        owner.request("/api/v1/subsidy/claims/batch-actions", batch_body, expected=403)
        batch_details_before = {f"/api/v1/subsidy/claims/{row['id']}": owner.request(f"/api/v1/subsidy/claims/{row['id']}") for row in batch_claims}
        batch_audit_path = "/api/v1/audits?action=subsidy.batch_review"
        batch_audit_before = reg.request(batch_audit_path)
        assert batch_audit_before["total"] == 1
        assert batch_audit_before["items"][0]["detail"]["count"] == 2
        business_csv_before = {}
        for base, rid in [("subsidy/claims",claim["id"]),("ratings/applications",rating["id"])]:
            for suffix in ["/export", "/process/export"]:
                path = f"/api/v1/{base}{suffix}?record_id={rid}"
                business_csv_before[path] = reg.request(path)
                assert business_csv_before[path].startswith(b"\xef\xbb\xbf")
                receipt = reg.request(f"/api/v1/subsidy/claims?record_id={claim['id']}")["items"][0]["last_submitted_at"]
                assert receipt is not None
                day = datetime.fromisoformat(receipt).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
                dated_path = path + f"&submitted_from={day}&submitted_to={day}"
                if base == "subsidy/claims":
                    dated_path += "&period_from=2026-11-02&period_to=2026-11-02"
                business_csv_before[dated_path] = reg.request(dated_path)
                if base == "ratings/applications":
                    assert len(business_csv_before[dated_path].decode("utf-8-sig").splitlines()) == 1

        staff_lookup_path = "/api/v1/accounts?" + urllib.parse.urlencode({"q": staff_username})
        staff_record = next(item for item in reg.request(staff_lookup_path)["items"] if item["username"] == staff_username)
        staff_original_password = staff_password
        staff_password = secrets.token_urlsafe(24)
        staff_reset = reg.request(f"/api/v1/accounts/{staff_record['id']}/password-reset", {
            "admin_password": source.values["DEMO_PASSWORD"], "temporary_password": staff_password,
            "version": staff_record["version"], "reason": "Synthetic restore owner verified",
        })
        assert staff_reset == {"ok": True, "user_id": staff_record["id"], "version": staff_record["version"] + 1, "must_change_password": True}
        staff_account.request("/api/v1/auth/me", expected=401)
        staff_record_before = reg.request(staff_lookup_path)
        staff_before = reg.request(staff_path)
        operations_paths = ["/api/v1/dashboard/operations?" + urllib.parse.urlencode({"period":period, "period_date":"2026-11-15"}) for period in ["day", "month", "year"]]
        quality_path = "/api/v1/statistics/quality?" + urllib.parse.urlencode({"source":"demo", "group_by":"station", "asset_scope":"connector", "start_at":"2026-11-01T08:00:00+08:00", "end_at":"2026-11-01T12:00:00+08:00", "page_size":100})
        facility_path = f"/api/v1/stations/{synced[0]['id']}"
        source_device = owner.request(f"/api/v1/stations/{synced[0]['id']}/equipment")["items"][0]
        assert source_device["equipment_type"] == "dc" and source_device["model_name"] == "DEMO-MODEL-DC"
        assert source_device["source_unique_code"] == "DEMO-REPORTED-ONLY" and source_device["manufactured_on"] == "2024-02-29"
        inventory_paths = [f"/api/v1/stations/{synced[0]['id']}/equipment/query?equipment_type=dc&connector_status=charging&page_size=1", f"/api/v1/stations/{synced[0]['id']}/equipment/{source_device['id']}/connectors?connector_status=charging&page_size=1"]
        history_energy_path = f"/api/v1/stations/{operating['id']}/energy-history?start_date=2026-11-01&end_date=2026-11-03"
        history_flow_path = f"/api/v1/stations/{station['id']}/flow?page_size=1"
        history_source_flow_path = f"/api/v1/stations/{synced[0]['id']}/flow?page_size=1"
        history_action_path = f"/api/v1/stations/{station['id']}/flow/{application['id']}/actions?page_size=1"
        history_paths = [history_energy_path, history_flow_path, history_source_flow_path, history_action_path]
        query_views_before = {}
        for path in ["/api/v1/map?zoom=19", "/api/v1/map/stations?location=all&page_size=100", "/api/v1/map/options",
                     "/api/v1/ratings/publications?page_size=100", "/api/v1/statistics?start_date=2026-11-01&end_date=2026-11-03",
                     "/api/v1/stations?page_size=100", "/api/v1/dashboard", f"/api/v1/stations/{synced[0]['id']}/equipment",
                     f"/api/v1/stations/{station['id']}", f"/api/v1/applications?application_id={application['id']}",
                     application_path, application_path+"/attachments", submissions_path, submission_path, application_path+"/review-context", quality_path, facility_path, *operations_paths, *inventory_paths, *history_paths]:
            view = owner.request(path, private=True)
            view.pop("generated_at", None)
            query_views_before[path] = view
        for path in operations_paths:
            view = query_views_before[path]
            assert view["current_stations"] >= 1 and isinstance(view["current_equipment"], int)
            assert view["source_completeness_verified"] is False and view["approvals"]["approval_rate_pct"] is None
        energy_history = query_views_before[history_energy_path]
        assert energy_history["station_id"] == operating["id"] and energy_history["source_completeness_verified"] is False
        assert any(stream["source"] == "demo" and stream["kwh"] == "120.050000" for stream in energy_history["streams"])
        flow_history = query_views_before[history_flow_path]
        assert flow_history["reporting_state"] is None and flow_history["last_access_approved_at"] is not None
        assert datetime.fromisoformat(query_views_before[history_source_flow_path]["source_first_reported_at"]) == datetime(2026,11,1,8,5,tzinfo=ZoneInfo("Asia/Shanghai"))
        assert datetime.fromisoformat(query_views_before[history_source_flow_path]["source_first_connected_at"]) == datetime(2026,11,1,8,0,tzinfo=ZoneInfo("Asia/Shanghai"))
        assert query_views_before[history_action_path]["total"] >= 3
        association.request(history_energy_path)
        for history_path in [history_flow_path,history_source_flow_path,history_action_path]:
            association.request(history_path,expected=403)
        for history_path in history_paths:
            other.request(history_path,expected=404)
        device_page = query_views_before[inventory_paths[0]]
        assert device_page["total"] == device_page["matching_connector_count"] == 1
        assert device_page["station_connector_count"] == 2
        assert device_page["items"][0]["manufactured_on"] == "2024-02-29"
        terminal_page = query_views_before[inventory_paths[1]]
        assert terminal_page["total"] == 1 and terminal_page["inventory_total"] == 2
        assert terminal_page["items"][0]["name"] == "演示2号终端"
        assert terminal_page["items"][0]["interface_type"] == "直流接口枪头（演示）"
        for inventory_path in inventory_paths:
            other.request(inventory_path, expected=404)
            association.request(inventory_path)
        facility = query_views_before[facility_path]
        assert facility["facilities"]["supply_capacity"] == "160.125"
        assert facility["facilities"]["supply_capacity_unit"] == "kVA"
        assert facility["facilities"]["independent_connection"] is False
        assert facility["facilities"]["parking_spaces"] == 0
        assert facility["facilities"]["commissioned_on"] == "2026-10-01"
        assert facility["contact_accessible"] is True and facility["contact"]["phone"] == "DEMO-NOT-A-REAL-NUMBER"
        association_facility = association.request(facility_path)
        assert association_facility["contact_accessible"] is False and association_facility["contact"] is None
        association.request(operations_paths[0], expected=403)
        quality_before = query_views_before[quality_path]
        assert quality_before["summary"]["station_count"] == quality_before["selected_station_count"]
        assert quality_before["total"] <= quality_before["page_size"]
        assert quality_before["summary"]["inventory_equipment_count"] == sum(item["inventory_equipment_count"] for item in quality_before["items"])
        assert quality_before["summary"]["streams"] and quality_before["source_completeness_verified"] is False
        quality_csv_path = quality_path.replace("/quality?", "/quality/export?")
        quality_csv_before = owner.request(quality_csv_path, expected_media="text/csv", private=True)
        assert "查询总计" in quality_csv_before.decode("utf-8-sig")
        combined_before = {}
        combined_files_before = {}
        for dimension in ["station", "equipment"]:
            path = "/api/v1/statistics/charging-subsidy?" + urllib.parse.urlencode({"period":"month", "period_date":"2026-11-15", "dimension":dimension, "page_size":100})
            combined_before[path] = owner.request(path, private=True)
            assert combined_before[path]["source_completeness_verified"] is False
            xlsx_path = path.replace("charging-subsidy?", "charging-subsidy/export?")
            combined_files_before[xlsx_path] = xlsx_cells(owner.request(xlsx_path, expected_media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", private=True))
        association.request("/api/v1/statistics/charging-subsidy?period_date=2026-11-15", expected=403)
        energy_csv_before = {}
        for path in ["/api/v1/statistics/export?start_date=2026-11-01&end_date=2026-11-03",
                     "/api/v1/statistics/analysis/export?start_date=2026-11-01&end_date=2026-11-03"]:
            energy_csv_before[path] = owner.request(path, expected_media="text/csv", private=True)
        map_csv_before = {}
        for location in ["all", "unlocated"]:
            path = "/api/v1/map/export?location=" + location
            map_csv_before[path] = owner.request(path, expected_media="text/csv", private=True)
        assert association.request("/api/v1/map/export?location=all", expected_media="text/csv", private=True).startswith(b"\xef\xbb\xbf")
        association.request("/api/v1/statistics/export?start_date=2026-11-01&end_date=2026-11-03", expected=403)
        pending_captcha = owner.request("/api/v1/auth/captcha")
        pending_seed = json.loads(source.run("exec","-T","api","python","-m","app.captcha_fixture","--container-preview","--",pending_captcha["captcha_id"],capture=True).stdout)
        source.backup(backup)
        print("Source workflow, worker and coordinated backup passed.", flush=True)
        # A non-empty destination is refused before any restore write.
        try:
            source.restore(backup)
            raise AssertionError("Non-empty restore target was accepted")
        except ValueError as error:
            assert str(error) == "Restore target not empty"
        target.up()
        corrupt = backup.with_name(backup.name + "-corrupt")
        shutil.copytree(backup, corrupt)
        with (corrupt / "database.dump").open("ab") as stream:
            stream.write(b"corruption")
        try:
            target.restore(corrupt)
            raise AssertionError("Corrupt backup was accepted")
        except ValueError:
            pass
        shutil.rmtree(corrupt)
        target.restore(backup)
        target.run("exec","-T","api","python","-c","from app.db import SessionLocal; from app.models import SmsChallenge,CaptchaChallenge,StaffApplicantSession; from sqlalchemy import select,func; db=SessionLocal(); assert db.scalar(select(func.count()).select_from(SmsChallenge))==0; assert db.scalar(select(func.count()).select_from(CaptchaChallenge))==0; assert db.scalar(select(func.count()).select_from(StaffApplicantSession))==0; db.close(); print('Restored SMS and image challenges revoked.')")
        restored = Client(target.values["PUBLIC_ORIGIN"], target)
        restored.request("/api/v1/health")
        # Backed-up authentication sessions must not be revived on the new stack.
        for cookie in owner.cookies:
            restored.cookies.set_cookie(cookie)
        restored.request("/api/v1/auth/me", expected=401)
        restored.request("/api/v1/auth/login", {"username":"operator_a", "password":owner_password, "captcha_id":pending_captcha["captcha_id"], "captcha_answer":pending_seed["answer"]}, expected=422)
        restored.cookies.clear()
        restored.login("operator_a", source.values["DEMO_PASSWORD"], expected=401)
        restored.login("operator_a", owner_password)
        restored_device_policies=[row for row in restored.request("/api/v1/subsidy/policies")["items"] if row["id"] in device_policy_ids]
        assert sorted(restored_device_policies,key=lambda row:row["id"])==device_policies_before
        restored.request("/api/v1/subsidy/claims",blocked_device_claim,expected=409)
        assert restored.request("/api/v1/auth/phone") == phone_before
        restored.request("/api/v1/auth/phone/change/confirm",{"challenge_id":pending_new_change["challenge_id"],"code":pending_new_code},expected=422)
        assert restored.request("/api/v1/auth/phone")==phone_before

        assert restored.request(executable_path)==executable_before
        assert restored.request(executable_path+"/device-lines")==executable_lines
        for statistics_path, expected_statistics in subsidy_statistics_before.items():
            assert restored.request(statistics_path) == expected_statistics

        restored.request("/api/v1/subsidy/claims",executable_payload,expected=409)
        assert restored.request(device_days_path)==device_days_before
        assert restored.request(device_days_csv_path)==device_days_csv_before
        runtime_after = restored.request(runtime_path)
        assert runtime_after["streams"] == runtime_before["streams"] and runtime_after["items"] == runtime_before["items"]
        assert runtime_after["total"] == runtime_before["total"]
        station_after = restored.request(f"/api/v1/stations/{station['id']}")
        assert station_after["status"] == "access_approved"
        assert hashlib.sha256(restored.request(download)).hexdigest() == attachment["sha256"]
        assert hashlib.sha256(restored.request(large_download)).hexdigest() == large["sha256"]
        assert restored.request(material_archive_path) == material_archive_before
        assert restored.request(submitted_rating_path) == submitted_rating_before
        assert restored.request(configured_path)==configured_before
        assert restored.request(configured_path+"/report")==configured_report
        restored_association=Client(target.values["PUBLIC_ORIGIN"],target)
        restored_association.login("association",source.values["DEMO_PASSWORD"])
        for path, detail_before in draft_details_before.items():
            assert restored_association.request(path)==detail_before
        assert restored_association.request(unused_standard_path)==archived_standard_before
        assert restored_association.request(referenced_standard_path)==disabled_standard_before
        restored.request(next(iter(draft_details_before)),expected=403)
        restored.request(unused_standard_path,expected=403)
        assert restored.request("/api/v1/subsidy/annual-batches",raw=annual_raw,content_type=annual_type,expected=201)=={"replayed":True,"receipt":annual_result["receipt"]}
        for path, before in annual_details.items():
            assert restored.request(path)==before
        for path, digest in annual_downloads:
            assert hashlib.sha256(restored.request(path)).hexdigest()==digest
        restored.request(f"/api/v1/attachments/{attachment['id']}", method="DELETE", expected=409)
        content_after = restored.request(content_path)
        assert content_after["rich_document"] == content_before["rich_document"]
        assert content_after["attachments"] == content_before["attachments"]
        for path,expected_hash in content_downloads:
            assert hashlib.sha256(restored.request(path)).hexdigest() == expected_hash
        restored_manager = Client(target.values["PUBLIC_ORIGIN"], target)
        restored_manager.login("regulator",source.values["DEMO_PASSWORD"])
        for path, before in query_views_before.items():
            view = restored.request(path, private=True)
            view.pop("generated_at", None)
            assert view == before, "Restored query changed: " + path
        assert restored.request(frozen_download, private=True) == payload
        assert hashlib.sha256(restored.request(frozen_archive_path, expected_media="application/zip", private=True)).hexdigest() == frozen_archive_digest
        restored_association.request(submission_path, expected=403)
        print("Application evidence restore: frozen source/payload/material classification, actual submission identity/time and exact private file/archive preserved; no legacy history fabricated.", flush=True)
        restored_facility = restored_association.request(facility_path)
        assert restored_facility["contact_accessible"] is False and restored_facility["contact"] is None
        restored_association.request(history_energy_path)
        for history_path in [history_flow_path,history_source_flow_path,history_action_path]:
            restored_association.request(history_path,expected=403)
        print("Station history restore: exact independent daily sources/nullable days, real application/action pages and distinct source connection/first-report clocks preserved; association flow denied.",flush=True)
        print("Device profile restore: source type/model/manufacturing date/unique code, terminal names/interface descriptions and bounded filtered inventory pages preserved.", flush=True)
        print("Station facility restore: exact source capacity/unit, false/zero, commissioning date, tariff/parking/profile fields and scoped contacts preserved.", flush=True)
        restored_association.request(operations_paths[0], expected=403)
        print("Operations restore: day/month/year inventory/distribution/approval proofs, independent order/day-energy sources and nullable 12-month trend preserved.", flush=True)
        assert restored.request(quality_csv_path, expected_media="text/csv", private=True) == quality_csv_before
        print("Quality summary restore: all-query inventory/observed streams/weighted recovery and detail+total CSV bytes preserved.", flush=True)
        for path, before in combined_before.items():
            assert restored.request(path, private=True) == before
        for path, before in combined_files_before.items():
            assert xlsx_cells(restored.request(path, expected_media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", private=True)) == before
        restored_association.request("/api/v1/statistics/charging-subsidy?period_date=2026-11-15", expected=403)
        print("Combined report restore: station/equipment scope, source/missing values/frozen money, all-query totals and canonical Excel cell values preserved; no workbook formulas.", flush=True)
        for path, before in energy_csv_before.items():
            assert restored.request(path, expected_media="text/csv", private=True) == before
        for path, before in map_csv_before.items():
            assert restored.request(path, expected_media="text/csv", private=True) == before
        assert "stations.export" in restored.request("/api/v1/auth/me")["permissions"]
        assert "stations.export" in restored_association.request("/api/v1/auth/me")["permissions"]
        assert "statistics.export" not in restored_association.request("/api/v1/auth/me")["permissions"]
        print("Map export restore: all/unlocated CSV bytes and independent station export grants preserved; association financial export remains unavailable.", flush=True)
        print("Query restore: 26 scoped inventory/dashboard/equipment/application/material/map/publication/energy views and both energy CSVs unchanged; private cache headers verified.", flush=True)
        assert restored_manager.request(sync_lookup_path) == sync_before
        assert restored_manager.request("/api/v1/sync/tasks", sync_body, expected=202)["id"] == task["id"]
        assert restored_manager.request(sync_lookup_path) == sync_before
        print("Sync restore: exact request lookup, completed counters and same-key replay preserved.", flush=True)
        assert next(item for item in restored_manager.request("/api/v1/alerts/rules")["items"] if item["id"] == legacy_rule["id"]) == legacy_rule_before
        assert restored_manager.request(legacy_event_path) == legacy_event_before
        assert restored_manager.request("/api/v1/alerts/samples", legacy_sample) == {"outcome": "ignored_stale", "event_id": legacy_first["event_id"]}
        assert restored_manager.request(legacy_event_path) == legacy_event_before
        print("Legacy alert restore: rule, event and cursor retained; repeated observation does not increment event.", flush=True)
        assert restored_manager.request(period_facts_path)==period_facts_before
        assert restored_manager.request(period_receipts_path)==period_receipts_before
        assert restored_manager.request(period_event_path)==period_event_before
        # Worker can tick after restoration without replaying the same fact version.
        target.run("exec","-T","api","python","-c","from app.alert_period_worker import process_one; process_one()")
        assert restored_manager.request(period_receipts_path)==period_receipts_before
        verify_restored_inspection_plans(restored_manager,restored,restored_association,inspection_plan_proof)
        verify_restored_inspection_query(restored,inspection_query_proof)
        verify_restored_protocol(restored_manager,restored,restored_association,protocol_proof)
        verify_restored_public_portal(restored_manager,public_portal_proof)
        restored_hazard_other=Client(target.values["PUBLIC_ORIGIN"],target)
        restored_hazard_other.login("operator_b",source.values["DEMO_PASSWORD"])
        verify_restored_hazard(restored_manager,restored,restored_hazard_other,restored_association,hazard_proof)
        assert restored_manager.request(inspection_path)==inspection_before
        assert restored.request(inspection_path+"/attachments")==inspection_materials_before
        for path,expected_digest in inspection_files_before.items():
            assert hashlib.sha256(restored.request(path)).hexdigest()==expected_digest
        # Restored historical data remains readable, but new writes cannot bypass facts.
        restored.request(inspection_path+"/actions",{"version":inspection_before["version"],"action":"reply","comment":"Incomplete new reply must not advance restored task"},expected=422)
        assert restored_manager.request(inspection_path)==inspection_before
        assert restored.request(inspection_path+"/attachments")==inspection_materials_before
        resumed_inspection=restored.request(inspection_path+"/actions",{"version":inspection_before["version"],"action":"reply","comment":"Synthetic correction continued after restore","contact":{"name":"Synthetic restored executor","phone":"13800000002"},"performed_at":(datetime.now(ZoneInfo("UTC"))-timedelta(hours=1)).isoformat()})
        finished_inspection=restored_manager.request(inspection_path+"/actions",{"version":resumed_inspection["version"],"action":"close","comment":"Synthetic restored evidence reviewed"})
        assert finished_inspection["status"]=="closed" and not finished_inspection["overdue"]
        inspection_after=restored.request(inspection_path)
        assert len(inspection_after["history"])==5
        assert inspection_after["history"][-2]["contact"]=={"name":"Synthetic restored executor","phone":"13800000002"}
        assert inspection_after["history"][-2]["performed_at"] is not None
        materials_after=restored.request(inspection_path+"/attachments")["items"]
        assert all(row["action_id"] is not None for row in materials_after)
        assert materials_after[0]["action_id"]==inspection_materials_before["items"][0]["action_id"]
        print("Inspection restore: structured requirement/reply/inline image, explicit executor/contact/user time and frozen/pending evidence preserved; reply and closure continued, original facts unchanged.",flush=True)
        assert restored_manager.request(enterprise_path)==enterprise_before
        assert hashlib.sha256(restored_manager.request(enterprise_file_path)).hexdigest()==enterprise_file_hash
        restored_enterprise=Client(target.values["PUBLIC_ORIGIN"],target)
        for cookie in enterprise_applicant.cookies: restored_enterprise.cookies.set_cookie(cookie)
        restored_enterprise.request("/api/v1/enrollment/me",expected=401)
        restored_enterprise.cookies.clear()
        enterprise_login=restored_enterprise.request("/api/v1/enrollment/login",{"username":registration_username,"password":registration_password})
        restored_enterprise.enrollment_csrf=enterprise_login["csrf_token"]
        assert {k:v for k,v in restored_enterprise.request("/api/v1/enrollment/me").items() if k!="csrf_token"}==enterprise_progress_before
        assert restored_enterprise.request("/api/v1/enrollment/me/attachments")==enterprise_materials_before
        enterprise_approved=restored_manager.request(enterprise_path+"/review",{"version":enterprise_before["version"],"action":"approve","comment":"Synthetic post-restore approval","role_profile_id":registration_role["id"],"role_profile_version":registration_role["version"]})
        assert enterprise_approved["status"]=="approved" and enterprise_approved["user_id"] is not None and enterprise_approved["operator_id"] is not None
        enterprise_business=Client(target.values["PUBLIC_ORIGIN"],target)
        enterprise_business.login(registration_username,registration_password)
        assert enterprise_business.request("/api/v1/auth/me")["permissions"]==["stations.read"]
        print("Enterprise enrollment restore: typed profile/history/material versions and private digest preserved; old applicant session revoked; approval and scoped first login passed.",flush=True)
        assert restored_manager.request(alert_configuration_path)==alert_configuration_before
        assert restored_manager.request(alert_csv_path)==alert_csv_before
        assert restored_manager.request(independent_path)==independent_before
        assert restored_manager.request(independent_records_path)==independent_records_before
        assert restored_manager.request("/api/v1/alerts/configured-observations",independent_body,expected=201)==independent_first
        assert restored_manager.request(notification_contacts_path)==notification_contacts_before
        assert restored_manager.request(notification_routes_path)==notification_routes_before
        assert restored_manager.request(notification_deliveries_path)==notification_deliveries_before
        restored_preference=next(row for row in restored_manager.request("/api/v1/alerts/notifications/preferences")["items"] if row["category"]=="device_charging")
        assert restored_preference==notification_preference_before


        restored_preview = restored_manager.request("/api/v1/alerts/configured-rules/preview",{"configuration":configured_alerts[0]["configuration"],"value":"80","coverage_complete":True})
        assert restored_preview["level"]=="critical"

        restored_manager.request(content_path+f"/attachments/{article_assets[0]['id']}?version=5",method="DELETE",expected=409)
        for path, content in business_csv_before.items():
            assert restored_manager.request(path) == content
        for path, detail_before in batch_details_before.items():
            assert restored_manager.request(path) == detail_before
        assert restored_manager.request(batch_audit_path) == batch_audit_before
        assert restored_manager.request(staff_path)==staff_before
        restored_applicant=Client(target.values["PUBLIC_ORIGIN"],target)
        for cookie in applicant.cookies: restored_applicant.cookies.set_cookie(cookie)
        restored_applicant.request("/api/v1/auth/staff-enrollment/me",expected=401)
        restored_staff=Client(target.values["PUBLIC_ORIGIN"],target)
        assert restored_manager.request(staff_lookup_path) == staff_record_before
        restored_staff.login(staff_username,staff_original_password,expected=401)
        restored_staff.login(staff_username,staff_password)
        assert restored_staff.request("/api/v1/auth/me")["must_change_password"]
        restored_staff.request("/api/v1/stations",expected=403)
        staff_final_password=secrets.token_urlsafe(24)
        assert restored_staff.request("/api/v1/auth/password",{"current_password":staff_password,"new_password":staff_final_password}) == {"ok": True, "reauthenticate": True}
        restored_staff.request("/api/v1/auth/me", expected=401)
        restored_staff.login(staff_username,staff_final_password)
        print("Password restore: admin reset version/temporary requirement preserved, old credential rejected; authenticated change revokes session and permits fresh login.", flush=True)
        identity=restored_staff.request("/api/v1/auth/me")
        assert identity["role"]=="association" and identity["allowed_districts"]==["鼓楼区"] and identity["permissions"]==["content.read","stations.read"]
        assert restored_staff.request("/api/v1/auth/phone")["verified"] is False
        restored_staff.request("/api/v1/staff-applications",expected=403)
        restored_staff.request("/api/v1/ratings/applications/export",expected=403)
        notice_after = restored_manager.request(notice_path+"?manage=true")
        assert restored_manager.request(deleted_notice_path+"?manage=true") == deleted_notice_before
        restored_manager.request(deleted_notice_path+"/actions", {"action":"publish", "version":2}, expected=409)
        assert restored_manager.request("/api/v1/content/notice-records/export?article_id="+str(notice["id"])) == notice_csv_before
        assert notice_after["deliveries"] == notice_before["deliveries"]
        assert notice_after["recipient_ids"] == notice_before["recipient_ids"]
        assert notice_after["replies"] == notice_before["replies"]
        assert restored.request(notice_path)["deliveries"] == owner_notice_before["deliveries"]
        assert restored.request(notice_path)["replies"] == owner_notice_before["replies"]
        for client, suffix in [(restored, ""), (restored_manager, "?manage=true")]:
            assert hashlib.sha256(client.request(notice_image_path + suffix, expected_media="image/png", private=True)).hexdigest() == reply_image["sha256"]
        restored_association.request(notice_image_path, expected=404)
        print("Structured reply restore: immutable formatting/inline image IDs, metadata/contact/hash preserved; owner/manager binary no-store and association denial verified.", flush=True)
        restored_manager.request(executable_path+"/actions",{"action":"approve","version":executable_before["version"],"comment":"Verify recovered source, cohort, historical rating and allocation"})
        print("Recovered executable device claim: frozen lines/amounts equal; duplicate blocked; evidence reverified on approval.",flush=True)
        recovered_independent = restored_manager.request("/api/v1/alerts/configured-observations",{**independent_body,"request_key":str(uuid4()),"value":"50","observed_at":"2026-11-01T08:03:00+08:00"},expected=201)
        assert recovered_independent["outcome"]=="recovered"
        assert restored_manager.request(notification_deliveries_path)==notification_deliveries_before
        assert restored_manager.request(notification_routes_path)["items"][0]["sms_status"]=="no_recipients"
        print("Notification restore: typed preferences, disabled receiver, old masked phone snapshots and simulated receipts preserved; no real SMS provider used.",flush=True)

        assert restored_manager.request(independent_path)["configuration_evidence"]["first"]==independent_before["configuration_evidence"]["first"]
        print("Independent alert restore: receipts/unknown coverage/frozen evidence preserved; replay idempotent; recovery continued.",flush=True)
        for kind in ["acceptance", "commission"]:
            continued = restored.request("/api/v1/applications", {"station_id": station["id"], "kind": kind, "payload": {"note": "Synthetic continued workflow after restore"}}, expected=201)
            continued_path = f"/api/v1/applications/{continued['id']}"
            continued = restored.request(continued_path, {"version": continued["version"], "note": "Edited after restore"}, method="PATCH")
            continued = restored.request(continued_path+"/actions", {"version": continued["version"], "action": "submit"})
            continued = restored_manager.request(continued_path+"/actions", {"version": continued["version"], "action": "approve", "comment": "Synthetic restored demo workflow"})
            assert continued["status"] == "approved" and continued["version"] == 4
        assert restored.request(f"/api/v1/stations/{station['id']}")["status"] == "operating"
        assert restored.request(application_path)["payload"] == application["payload"]
        assert restored.request(download) == payload
        print("Application restore: original edited/history/material proof retained; acceptance and commissioning continue through typed edit/submit/review responses.", flush=True)
        target.run("exec", "-T", "api", "python", "-c", role_check)
        target.tool("python", "-m", "app.backup_integrity")
        print(
            "Restore proof passed: state, private-file digest, session revocation, SQL privileges, runtime observations/exclusion, structured articles/private assets and frozen evidence.",
            flush=True,
        )
    finally:
        for stack in [source, target]:
            stack.run("down", "--volumes", "--remove-orphans")
        print("Only the two newly created verification projects/volumes were removed.", flush=True)


if __name__ == "__main__":
    main()
