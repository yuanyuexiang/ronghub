"""Exercise two isolated local Docker stacks, including real DB+attachment recovery.

Build images first: python3 scripts/stack_ops.py up --build
This check creates only random ronghub-verify-* / ronghub-restore-* fixtures and removes their volumes.
"""

import base64
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
from zoneinfo import ZoneInfo

from stack_ops import ROOT, Stack, initialize


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

    def request(self, path, body=None, method=None, raw=None, content_type=None, expected=200):
        headers = {}
        if body is not None:
            raw = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        if body is not None or raw is not None or method in {"POST", "PATCH", "DELETE"}:
            headers.update({"x-csrf-token": self.csrf, "Origin": self.origin})
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
        owner.request(
            f"/api/v1/applications/{application['id']}/actions", {"action": "submit", "version": 1}
        )
        reg.request(
            f"/api/v1/applications/{application['id']}/actions",
            {"action": "approve", "version": 2, "comment": "Docker demo approval"},
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
        task = reg.request(
            "/api/v1/sync/tasks",
            {"operator_id": owner_info["operator_id"], "request_key": "verify-" + key},
            expected=202,
        )
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
        owner.request(notice_path+"/replies", {"body":"恢复前回复", "contact_name":"测试联系人", "contact_phone":"13800000000"}, expected=201)
        notice_csv_before = reg.request("/api/v1/content/notice-records/export?article_id="+str(notice["id"]))
        notice_before = reg.request(notice_path+"?manage=true")
        owner_notice_before = owner.request(notice_path)
        assert len(notice_before["deliveries"]) == len(notice_ids)
        assert len(owner_notice_before["deliveries"]) == 1
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
        phone_before = owner.request("/api/v1/auth/phone")
        assert phone_before["verified"] and phone_before["verification_mode"] == "demo"
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
        operating = next(station for station in own if station["status"] == "operating")
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
        restored_staff.login(staff_username,staff_password)
        assert restored_staff.request("/api/v1/auth/me")["must_change_password"]
        restored_staff.request("/api/v1/stations",expected=403)
        staff_final_password=secrets.token_urlsafe(24)
        restored_staff.request("/api/v1/auth/password",{"current_password":staff_password,"new_password":staff_final_password})
        restored_staff.login(staff_username,staff_final_password)
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
