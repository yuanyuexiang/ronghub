"""Restore populated historical schemas into an empty current stack (local demo only).

These are synthetic migration fixtures, not customer archives or old application images.
Uses random owned stacks only. Build the current API image before running.
"""

import argparse
import hashlib
import json
import secrets
from datetime import datetime, timezone

from stack_ops import ROOT, Stack, digest, initialize
from verify_stack import Client, available_port

FIXTURE = r"""
import hashlib, json, sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from sqlalchemy import MetaData, Table, insert
from app.db import engine
from app.dependencies import passwords
from app.config import settings
data=json.load(sys.stdin)
stamp=datetime.now(timezone.utc)
with engine.begin() as c:
    md=MetaData()
    def add(table_name, **values):
        t=Table(table_name,md,autoload_with=c)
        return c.scalar(insert(t).values(**values).returning(t.c[list(t.primary_key.columns)[0].name]))
    op=add('operators', name='Legacy fixture company')
    profile=add('role_profiles',name='Legacy district reader',base_role='operator',permissions=['stations.read'],version=1)
    user=add('users',username='legacy-owner',password_hash=passwords.hash(data['password']),role='operator',operator_id=op,enabled=True,role_profile_id=profile,allowed_districts=['鼓楼区'])
    reg=add('users',username='legacy-regulator',password_hash=passwords.hash(data['password']),role='regulator',enabled=True)
    station=add('stations',operator_id=op,source='legacy-fixture',external_id='legacy-1',name='Legacy station',district='鼓楼区',address='Fixture address',status='pending',connectors=2,power_kw='120.25',version=3,synced_at=stamp)
    application=add('station_applications',station_id=station,operator_id=op,applicant_id=user,kind='access',status='submitted',payload={'note':'legacy evidence'},version=2,rule_version='demo-workflow-v1',created_at=stamp,updated_at=stamp)
    content=b'%PDF-1.4\nLegacy recovery evidence\n%%EOF\n'
    key='a'*48
    Path(settings.storage_path,key).write_bytes(content)
    attachment=add('attachments',application_id=application,uploaded_by=user,storage_key=key,filename='legacy-proof.pdf',content_type='application/pdf',size=len(content),sha256=hashlib.sha256(content).hexdigest(),created_at=stamp)
    article=add('articles',kind='announcement',title='Legacy plain text',body='<b>literal text</b>',audience='all',status='published',published_at=stamp,version=2,created_by=reg,created_at=stamp,is_demo=True)
    standard=add('rating_standards',code='LEGACY',version=1,name='Legacy example',rules={},is_demo=True,enabled=True,created_at=stamp)
    application_rating=add('rating_applications',station_id=station,operator_id=op,standard_id=standard,status='withdrawn',version=2,initial_scores={},current_scores={},calculation={'total':None,'stars':None},rule_snapshot={},evidence_note='',is_demo=True,created_at=stamp)
    add('rating_actions',application_id=application_rating,actor_id=user,action='withdraw',comment='Historical action without transition snapshot',payload={},created_at=stamp)
    old_policy=add('subsidy_policies',code='LEGACY-SUBSIDY',version=1,name='Legacy subsidy',rate='0.1',effective_from=date(2026,1,1),effective_to=date(2026,12,31),enabled=True,is_demo=True,created_at=stamp)
    old_claim=add('subsidy_claims',station_id=station,operator_id=op,policy_id=old_policy,start_date=date(2026,11,1),end_date=date(2026,11,2),status='approved',total_kwh='10',base_amount='1.00',adjusted_amount='1.00',snapshot={},version=3,is_demo=True,created_at=stamp)
    add('subsidy_actions',claim_id=old_claim,actor_id=reg,action='approve',comment='Historical record without statement/evidence snapshot',delta='0.00',created_at=stamp)
    add('login_sessions',token_hash=hashlib.sha256(data['token'].encode()).hexdigest(),user_id=user,csrf='legacy-csrf',expires_at=stamp+timedelta(hours=1))
print(json.dumps({'operator':op,'user':user,'station':station,'application':application,'attachment':attachment,'article':article,'rating':application_rating,'subsidy':old_claim,'digest':hashlib.sha256(content).hexdigest()}))
"""


def verify(revision):
    key = secrets.token_hex(5)
    source_env = ROOT / f"deploy/.env.legacy-source-{key}"
    target_env = ROOT / f"deploy/.env.legacy-target-{key}"
    initialize(source_env, "ronghub-legacy-source-" + key, available_port())
    initialize(target_env, "ronghub-legacy-target-" + key, available_port())
    source, target = Stack(source_env), Stack(target_env)
    archive = ROOT / ".backups" / f"legacy-{revision}-{key}"
    archive.mkdir(mode=0o700)
    password, old_token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    try:
        source.run("up", "-d", "--wait", "postgres")
        source.run(
            "run", "--rm", "--no-deps", "-T", "migrate", "alembic", "upgrade", revision
        )
        # migrate has owner DB access; api owns the attachments volume. No runtime source service.
        source.tool("mkdir", "-p", "/var/lib/ronghub/attachments")
        fixture = source.run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-v",
            f"{source.values['COMPOSE_PROJECT_NAME']}_attachments:/var/lib/ronghub/attachments",
            "migrate",
            "python",
            "-c",
            FIXTURE,
            input=json.dumps({"password": password, "token": old_token}).encode(),
            capture=True,
        )
        expected = json.loads(fixture.stdout)
        with (archive / "database.dump").open("xb") as stream:
            source.run(
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "ronghub_owner",
                "-d",
                "ronghub",
                "-Fc",
                "--no-owner",
                "--no-acl",
                stdout=stream,
            )
        with (archive / "attachments.tar").open("xb") as stream:
            source.tool(
                "tar",
                "-C",
                "/var/lib/ronghub/attachments",
                "-cf",
                "-",
                ".",
                stdout=stream,
            )
        manifest = {
            "format": 1,
            "postgres_major": 17,
            "revision": revision,
            "source_project": source.values["COMPOSE_PROJECT_NAME"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "quiesced_writers": [],
            "files": {
                name: {
                    "sha256": digest(archive / name),
                    "size": (archive / name).stat().st_size,
                }
                for name in ["database.dump", "attachments.tar"]
            },
        }
        (archive / "manifest.json").write_text(json.dumps(manifest, indent=2))
        for path in archive.iterdir():
            path.chmod(0o600)
        target.up()
        target.restore(archive)
        # Restore deliberately cannot manufacture identity proofs or notice recipient history.
        proof = target.run(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            """
from sqlalchemy import text
from app.db import engine
from alembic.config import Config
from alembic.script import ScriptDirectory
with engine.connect() as c:
    assert c.scalar(text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(Config('alembic.ini')).get_current_head()
    for name in ['login_sessions','captcha_challenges','sms_challenges','staff_applicant_sessions','verified_phones','notice_deliveries']:
        assert c.scalar(text('SELECT count(*) FROM '+name)) == 0, name
    assert c.scalar(text('SELECT rich_document FROM articles LIMIT 1')) is None
print('Historical state upgraded without invented proofs or recipients.')
""",
            capture=True,
        )
        print(proof.stdout.decode().strip(), flush=True)
        manager = Client(target.values["PUBLIC_ORIGIN"], target)
        manager.login("legacy-regulator", password)
        owner = Client(target.values["PUBLIC_ORIGIN"], target)
        owner.login("legacy-owner", password)
        identity = owner.request("/api/v1/auth/me")
        assert identity["permissions"] == ["stations.read"]
        assert identity["allowed_districts"] == ["鼓楼区"]
        stations = owner.request("/api/v1/stations")["items"]
        assert len(stations) == 1 and stations[0]["id"] == expected["station"]
        assert str(stations[0]["power_kw"]) == "120.25"
        owner.request("/api/v1/staff-applications", expected=403)
        article = manager.request(f"/api/v1/content/{expected['article']}?manage=true")
        assert (
            article["body"] == "<b>literal text</b>"
            and article["rich_document"] is None
        )
        legacy_standard = manager.request("/api/v1/ratings/standards")["items"][0]
        assert legacy_standard["activation_state"]=="legacy_unknown" and legacy_standard["effective_on"] is None
        assert legacy_standard["rules"]["configuration_version"] is None
        assert legacy_standard["rules"]["indicator_status_version"] is None
        assert legacy_standard["revision"]==1 and legacy_standard["archived"] is False
        legacy_policy = next(item for item in manager.request("/api/v1/subsidy/policies")["items"] if item["code"]=="LEGACY-SUBSIDY")
        assert legacy_policy["device_rules"] is None and legacy_policy["claim_supported"] and legacy_policy["calculation_basis"]=="legacy_station_energy"
        assert manager.request("/api/v1/subsidy/device-days?start_date=2026-11-01&end_date=2026-11-03")["total"]==0
        old_subsidy=manager.request(f"/api/v1/subsidy/claims/{expected['subsidy']}")
        assert old_subsidy["application_note"] is None and old_subsidy["snapshot"]["submission"] is None and old_subsidy["snapshot"]["application_year"] is None
        assert manager.request(f"/api/v1/subsidy/claims/{expected['subsidy']}/attachments")["items"] == []
        legacy_detail = manager.request(f"/api/v1/ratings/applications/{expected['rating']}")
        assert legacy_detail["rule_snapshot"]["indicators"] is None
        assert legacy_detail["calculation"]["total"] is None
        assert legacy_detail["last_submission"] is None
        assert legacy_detail["station_name"] == "Legacy station"
        assert legacy_detail["operator_name"] == "Legacy fixture company"
        legacy_queue = manager.request(f"/api/v1/ratings/applications?record_id={expected['rating']}")
        assert legacy_queue["items"][0]["last_submitted_at"] is None
        assert manager.request(f"/api/v1/ratings/applications?record_id={expected['rating']}&submitted_from=2026-11-15")["total"] == 0
        legacy_process = manager.request(f"/api/v1/ratings/applications/process/export?record_id={expected['rating']}")
        assert "旧记录未留存" in legacy_process.decode("utf-8-sig")
        material = manager.request(
            f"/api/v1/attachments/{expected['attachment']}/download"
        )
        assert hashlib.sha256(material).hexdigest() == expected["digest"]
        print(
            f"PASS {revision} → current head: real archive restore, private PDF, plain content, scoped login and session revocation.",
            flush=True,
        )
    finally:
        for stack in [source, target]:
            stack.run("down", "--volumes", "--remove-orphans")
        print(
            "Only this legacy verification's two projects/volumes were removed.",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revisions",
        nargs="+",
        choices=["0031", "0035", "0039"],
        default=["0031", "0035", "0039"],
    )
    for revision in parser.parse_args().revisions:
        verify(revision)
