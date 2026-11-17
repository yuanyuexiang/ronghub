"""Local demo only: verify full migration chain and legacy rating upgrade in a temporary schema.

Run: uv run python -m app.migration_verify (from apps/server).
The current application schema is never migrated or dropped by this command.
"""

import json
import os
import secrets
import subprocess
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import settings
from app.db import engine
from app.device_metadata import CONNECTOR_FIELDS, EQUIPMENT_FIELDS
from app.models import (
    ApplicationSubmission,
    Article,
    Attachment,
    Connector,
    Equipment,
    HazardCase,
    InspectionAction,
    InspectionPlan,
    InspectionTask,
    Operator,
    RatingApplication,
    Station,
    now,
)
from app.station_facilities import FIELDS


def main():
    url = make_url(settings.database_url)
    if not settings.demo_mode or url.host not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Migration verification requires a local demo database")
    if url.query.get("options"):
        raise SystemExit(
            "Verification expects the standard local connection without custom options"
        )
    schema = "ronghub_verify_" + secrets.token_hex(8)
    isolated = url.update_query_dict({"options": "-csearch_path=" + schema})
    scratch = create_engine(isolated)
    env = os.environ.copy()
    env["DATABASE_URL"] = isolated.render_as_string(hide_password=False)
    root = Path(__file__).resolve().parents[1]
    created = False
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        subprocess.run(["uv", "run", "alembic", "upgrade", "0015"], cwd=root, env=env, check=True)
        expected = {}
        with Session(scratch) as db, db.begin():
            op = Operator(name="migration-verification")
            db.add(op)
            db.flush()
            actor = SimpleNamespace(
                id=db.scalar(
                    text(
                        "INSERT INTO users (username,password_hash,role,enabled) VALUES ('migration-verification','unused','association',true) RETURNING id"
                    )
                )
            )
            for base, grants in [
                (
                    "regulator",
                    [
                        "stations.read",
                        "subsidy.read",
                        "alerts.read",
                        "alerts.write",
                        "content.read",
                        "content.write",
                        "subsidy.write",
                        "applications.read",
                        "applications.write",
                        "ratings.read",
                        "ratings.write",
                        "inspections.read",
                        "inspections.write",
                    ],
                ),
                (
                    "operator",
                    [
                        "stations.read",
                        "subsidy.read",
                        "subsidy.write",
                        "applications.read",
                        "applications.write",
                        "ratings.read",
                        "ratings.write",
                        "inspections.read",
                        "inspections.write",
                    ],
                ),
                ("association", ["stations.read", "ratings.read", "ratings.write"]),
            ]:
                db.execute(
                    text(
                        "INSERT INTO role_profiles(name,base_role,permissions,version) VALUES (:name,:base,CAST(:grants AS JSON),1)"
                    ),
                    {"name": "migration-" + base, "base": base, "grants": json.dumps(grants)},
                )
            db.execute(
                text(
                    "INSERT INTO role_profiles(name,base_role,permissions,version) VALUES ('migration-readonly','regulator','[\"stations.read\"]',1)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO login_sessions(token_hash,user_id,csrf,expires_at) VALUES (:token,:uid,'fixture-csrf',now()+interval '1 hour')"
                ),
                {"token": "a" * 64, "uid": actor.id},
            )
            registration_id = db.scalar(
                text(
                    "INSERT INTO registrations(username,password_hash,company_name,credit_code,registration_address,contacts,status,version,created_at,is_demo) VALUES ('migration-applicant','unused','migration-company','000000000000000000','','[]','draft',1,now(),true) RETURNING id"
                )
            )
            db.execute(
                text(
                    "INSERT INTO enrollment_sessions(token_hash,registration_id,csrf,expires_at) VALUES (:token,:rid,'fixture-csrf',now()+interval '1 hour')"
                ),
                {"token": "b" * 64, "rid": registration_id},
            )
            # Populate the actual historical shape; current ORM control columns did not exist yet.
            standard_id = db.scalar(
                text(
                    "INSERT INTO rating_standards (code,version,name,rules,is_demo,enabled,created_at) VALUES ('migration-verification',1,'Upgrade fixture','{}',true,true,:stamp) RETURNING id"
                ),
                {"stamp": now()},
            )
            for index in range(2):
                station_id = db.scalar(
                    text("""INSERT INTO stations
                    (operator_id, source, external_id, name, district, address, status, connectors, power_kw, version, synced_at)
                    VALUES (:operator, 'migration-verification', :external, 'Upgrade fixture', 'test', 'test', 'operating', 0, 0, 1, :synced)
                    RETURNING id"""),
                    {"operator": op.id, "external": str(index), "synced": now()},
                )
                if index == 0:
                    legacy_inspection_id = db.scalar(
                        text(
                            "INSERT INTO inspection_tasks (station_id,operator_id,title,description,severity,due_at,status,version,created_by,created_at,is_demo) VALUES (:sid,:oid,'Legacy inspection','Legacy plain requirement','major',:at,'replied',2,:uid,:at,true) RETURNING id"
                        ),
                        {"sid": station_id, "oid": op.id, "uid": actor.id, "at": now()},
                    )
                    legacy_inspection_action_id = db.scalar(
                        text(
                            "INSERT INTO inspection_actions (task_id,actor_id,action,comment,created_at) VALUES (:tid,:uid,'reply','Legacy plain feedback',:at) RETURNING id"
                        ),
                        {"tid": legacy_inspection_id, "uid": actor.id, "at": now()},
                    )
                    legacy_application_id = db.scalar(
                        text(
                            "INSERT INTO station_applications (station_id,operator_id,applicant_id,kind,status,payload,version,rule_version,created_at,updated_at) VALUES (:sid,:oid,:uid,'access','draft','{}',1,'demo-workflow-v1',:at,:at) RETURNING id"
                        ),
                        {"sid": station_id, "oid": op.id, "uid": actor.id, "at": now()},
                    )
                    legacy_attachment_id = db.scalar(
                        text(
                            "INSERT INTO attachments (application_id,uploaded_by,storage_key,filename,content_type,size,sha256,created_at) VALUES (:aid,:uid,:key,'migration.pdf','application/pdf',10,:digest,:at) RETURNING id"
                        ),
                        {
                            "aid": legacy_application_id,
                            "uid": actor.id,
                            "key": "a" * 48,
                            "digest": "b" * 64,
                            "at": now(),
                        },
                    )
                    legacy_device_id = db.scalar(
                        text(
                            "INSERT INTO equipment (station_id,external_id,name,power_kw) VALUES (:id,'old-device','Old device',60) RETURNING id"
                        ),
                        {"id": station_id},
                    )
                    legacy_connector_id = db.scalar(
                        text(
                            "INSERT INTO connectors (equipment_id,external_id,status) VALUES (:id,'old-terminal','unknown') RETURNING id"
                        ),
                        {"id": legacy_device_id},
                    )
                ids = []
                # Station 0: older application was published later. Station 1: latest result delisted.
                for position in range(2):
                    app_id = db.scalar(
                        text("""INSERT INTO rating_applications
                        (station_id, operator_id, standard_id, status, version,
                         initial_scores, current_scores, calculation, rule_snapshot,
                         evidence_note, is_demo, created_at)
                        VALUES (:station, :operator, :standard, :status, 4,
                            '{}', '{}', :calculation, '{}', '', true, :created)
                        RETURNING id"""),
                        {
                            "station": station_id,
                            "operator": op.id,
                            "standard": standard_id,
                            "status": "delisted" if index == 1 and position == 1 else "published",
                            "calculation": json.dumps({"stars": 3}),
                            "created": now() - timedelta(days=5),
                        },
                    )
                    ids.append(app_id)
                    offset = (2 - position) if index == 0 else position
                    db.execute(
                        text("""INSERT INTO rating_actions (application_id, actor_id, action, comment, payload, created_at)
                        VALUES (:app, :actor, 'publish', 'migration fixture', '{}', :time)"""),
                        {
                            "app": app_id,
                            "actor": actor.id,
                            "time": now() - timedelta(days=4) + timedelta(hours=offset),
                        },
                    )
                expected[index] = ids
        subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=root, env=env, check=True)
        subprocess.run(["uv", "run", "alembic", "check"], cwd=root, env=env, check=True)
        with Session(scratch) as db:
            rows = {a.id: a for a in db.scalars(select(RatingApplication))}
            grants = {
                r["base_role"]: r
                for r in db.execute(
                    text(
                        "SELECT base_role,permissions,version FROM role_profiles WHERE name != 'migration-readonly'"
                    )
                ).mappings()
            }
            assert set(grants["operator"]["permissions"]) == {
                "stations.read",
                "subsidy.read",
                "subsidy.create",
                "subsidy.submit",
                "subsidy.withdraw",
                "applications.read",
                "applications.create",
                "applications.edit",
                "applications.materials",
                "applications.submit",
                "applications.withdraw",
                "ratings.read",
                "ratings.create",
                "ratings.edit",
                "ratings.materials",
                "ratings.submit",
                "ratings.withdraw",
                "inspections.read",
                "inspections.materials",
                "inspections.reply",
            }
            assert (
                "subsidy.adjust" in grants["regulator"]["permissions"]
                and "subsidy.write" not in grants["regulator"]["permissions"]
            )
            assert (
                grants["operator"]["version"] == 5
                and grants["regulator"]["version"] == 7
                and grants["association"]["version"] == 2
            )
            assert {
                "inspections.assign",
                "inspections.materials",
                "inspections.reply",
                "inspections.return",
                "inspections.close",
            }.issubset(grants["regulator"]["permissions"])
            assert "inspections.write" not in grants["regulator"]["permissions"]
            assert {"alerts.read", "alerts.rules", "alerts.samples", "alerts.acknowledge"}.issubset(
                grants["regulator"]["permissions"]
            )
            assert "alerts.write" not in grants["regulator"]["permissions"]
            assert {
                "content.read",
                "content.manage",
                "content.create",
                "content.edit",
                "content.publish",
                "content.withdraw",
            }.issubset(grants["regulator"]["permissions"])
            assert "content.write" not in grants["regulator"]["permissions"]
            assert "content.reply" not in grants["regulator"]["permissions"]
            assert not any(cap.startswith("alerts.") for cap in grants["operator"]["permissions"])
            assert "applications.review" in grants["regulator"]["permissions"]
            assert "applications.review" not in grants["operator"]["permissions"]
            assert "applications.write" not in grants["operator"]["permissions"]
            assert set(grants["association"]["permissions"]) == {
                "stations.read",
                "ratings.read",
                "ratings.review",
                "ratings.publish",
                "ratings.delist",
                "ratings.standards",
            }
            assert (
                db.scalar(text("SELECT version FROM role_profiles WHERE name='migration-readonly'"))
                == 1
            )
            assert db.scalar(
                text("SELECT permissions FROM role_profiles WHERE name='migration-readonly'")
            ) == ["stations.read"]
            assert db.scalar(text("SELECT count(*) FROM login_sessions")) == 0
            assert db.scalar(text("SELECT count(*) FROM enrollment_sessions")) == 0

            assert (
                db.scalar(text("SELECT bool_and(must_change_password = false) FROM users")) is True
            )
            assert all(
                station.longitude is None
                and station.latitude is None
                and station.coordinate_system == "unknown"
                and station.station_type == "unknown"
                and station.location_is_demo is False
                and station.usage_type == "unknown"
                and station.construction_site == "unknown"
                and station.site_state == "unknown"
                and station.source_sync_state == "unknown"
                and station.source_first_connected_at is None
                and station.source_first_reported_at is None
                and all(getattr(station, key) is None for key in FIELDS)
                for station in db.scalars(select(Station))
            )
            assert db.get(InspectionTask, legacy_inspection_id).rich_document is None
            assert db.get(InspectionTask, legacy_inspection_id).plan_id is None
            assert db.get(InspectionTask, legacy_inspection_id).due_at is not None
            assert db.scalar(select(func.count()).select_from(InspectionPlan)) == 0
            old_execution = db.get(InspectionAction, legacy_inspection_action_id)
            assert all(
                getattr(old_execution, key) is None
                for key in ["rich_document", "contact_name", "contact_phone", "performed_at"]
            )
            legacy_material = db.get(Attachment, legacy_attachment_id)
            assert (
                legacy_material.active is True
                and legacy_material.category is None
                and legacy_material.revision == 1
            )
            assert db.scalar(select(func.count()).select_from(ApplicationSubmission)) == 0
            legacy_device = db.get(Equipment, legacy_device_id)
            legacy_connector = db.get(Connector, legacy_connector_id)
            assert all(getattr(legacy_device, key) is None for key in EQUIPMENT_FIELDS)
            assert all(getattr(legacy_connector, key) is None for key in CONNECTOR_FIELDS)
            constraints = {
                constraint["name"]
                for constraint in inspect(scratch).get_check_constraints("stations")
            }
            assert {
                "ck_station_coordinate_pair",
                "ck_station_coordinate_system",
                "ck_station_type",
                "ck_station_usage_type",
                "ck_station_construction_site",
                "ck_station_site_state",
                "ck_station_source_sync_state",
                "ck_station_facility_supply_type",
                "ck_station_facility_electricity_tariff",
                "ck_station_facility_capacity",
                "ck_station_facility_parking_spaces",
                "ck_station_facility_commissioned_on",
                "ck_station_facility_parking_fee_type",
            }.issubset(constraints)
            runtime_constraint = db.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'runtime_intervals'::regclass AND conname = 'ex_runtime_no_overlap'"
                )
            )
            assert (
                "EXCLUDE USING gist" in runtime_constraint
                and "tstzrange" in runtime_constraint
                and "DEFERRABLE" in runtime_constraint
            )
            assert (
                db.scalar(
                    text(
                        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='btree_gist'"
                    )
                )
                == "public"
            )
            first, second = expected[0]
            assert rows[first].status == "published" and rows[second].status == "superseded"
            first, second = expected[1]
            assert rows[first].status == "superseded" and rows[second].status == "delisted"
            assert all(
                a.published_at and a.valid_until is None and a.report_snapshot is None
                for a in rows.values()
            )

        # Exercise the new downgrade guard against actual data, not just its source text.
        with scratch.connect() as connection:
            protected_head = connection.scalar(text("SELECT version_num FROM alembic_version"))

        def downgrade_refused():
            result = subprocess.run(
                ["uv", "run", "alembic", "downgrade", "0043"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode != 0 and "Refuse downgrade" in result.stderr
            with scratch.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == protected_head
                )
                assert "rating_standard_drafts" in inspect(connection).get_table_names()

        with Session(scratch) as db, db.begin():
            assert all(not row.portal_public for row in db.scalars(select(Article)))
            public = Article(
                kind="news",
                title="Migration explicit public",
                body="Preserve public consent",
                portal_public=True,
                created_by=actor.id,
            )
            db.add(public)
            db.flush()
            public_id = public.id
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            assert db.get(Article, public_id).portal_public is True
            db.delete(db.get(Article, public_id))
        print(
            "Verified every legacy article remains private and explicit public consent blocks destructive downgrade."
        )

        with Session(scratch) as db, db.begin():
            protocol = Article(
                kind="protocol",
                title="Migration customer protocol",
                body="Preserve protocol",
                created_by=actor.id,
            )
            db.add(protocol)
            db.flush()
            protocol_id = protocol.id
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            assert db.get(Article, protocol_id).kind == "protocol"
            db.delete(db.get(Article, protocol_id))
        print(
            "Verified protocol content blocks downgrade to readers that do not recognize its type; no official protocol seeded."
        )

        with Session(scratch) as db, db.begin():
            legacy = db.get(InspectionTask, legacy_inspection_id)
            assert db.scalar(select(func.count()).select_from(HazardCase)) == 0
            hazard = HazardCase(
                station_id=legacy.station_id,
                operator_id=legacy.operator_id,
                station_name="Migration station",
                operator_name="Migration operator",
                title="Migration hazard",
                description="Do not drop real supervision",
                severity="major",
                due_at=now() + timedelta(days=1),
                created_by=actor.id,
            )
            db.add(hazard)
            db.flush()
            hazard_id = hazard.id
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            hazard = db.get(HazardCase, hazard_id)
            assert hazard.status == "tracking" and hazard.progress == "not_started"
            db.delete(hazard)
        print(
            "Verified ordinary legacy inspections are not reclassified and populated hazard supervision blocks destructive downgrade."
        )

        with Session(scratch) as db, db.begin():
            draft = InspectionPlan(created_by=actor.id, request_key=str(uuid4()))
            db.add(draft)
            db.flush()
            draft_id = draft.id
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            assert db.get(InspectionPlan, draft_id).status == "draft"
            db.delete(db.get(InspectionPlan, draft_id))
            legacy_task = db.get(InspectionTask, legacy_inspection_id)
            original_due_at = legacy_task.due_at
            legacy_task.due_at = None
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            legacy_task = db.get(InspectionTask, legacy_inspection_id)
            assert legacy_task.due_at is None
            legacy_task.due_at = original_due_at
        print(
            "Verified no fabricated legacy plan links/deadlines; private plans and unknown deadlines block destructive downgrade."
        )

        for model, identifier, changes, reset in [
            (
                InspectionTask,
                legacy_inspection_id,
                {
                    "rich_document": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Protected requirement"}],
                            }
                        ],
                    }
                },
                {"rich_document": None},
            ),
            (
                InspectionAction,
                legacy_inspection_action_id,
                {
                    "rich_document": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Protected reply"}],
                            }
                        ],
                    }
                },
                {"rich_document": None},
            ),
            (
                InspectionAction,
                legacy_inspection_action_id,
                {"contact_name": "Executor", "contact_phone": "13800000001"},
                {"contact_name": None, "contact_phone": None},
            ),
            (
                InspectionAction,
                legacy_inspection_action_id,
                {"performed_at": now()},
                {"performed_at": None},
            ),
        ]:
            with Session(scratch) as db, db.begin():
                row = db.get(model, identifier)
                for key, value in changes.items():
                    setattr(row, key, value)
            downgrade_refused()
            with Session(scratch) as db, db.begin():
                row = db.get(model, identifier)
                for key, value in changes.items():
                    assert getattr(row, key) == value
                for key, value in reset.items():
                    setattr(row, key, value)
        print(
            "Verified legacy inspection formatting/contact/execution time remain unknown and every new fact blocks destructive downgrade."
        )

        with Session(scratch) as db, db.begin():
            proof = ApplicationSubmission(
                application_id=legacy_application_id,
                submitted_version=2,
                actor_id=actor.id,
                actor_username="migration-verification",
                submitted_at=now(),
                source_reviewed=False,
                content_digest="0" * 64,
                snapshot={
                    "schema_version": 1,
                    "source_profile": None,
                    "payload": {},
                    "materials": [],
                },
            )
            db.add(proof)
            db.flush()
            proof_id = proof.id
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            assert db.get(ApplicationSubmission, proof_id).snapshot["schema_version"] == 1
            db.delete(db.get(ApplicationSubmission, proof_id))
        for field, value, restore in [
            ("active", False, True),
            ("category", "project_filing", None),
            ("revision", 2, 1),
        ]:
            with Session(scratch) as db, db.begin():
                setattr(db.get(Attachment, legacy_attachment_id), field, value)
            downgrade_refused()
            with Session(scratch) as db, db.begin():
                row = db.get(Attachment, legacy_attachment_id)
                assert getattr(row, field) == value
                setattr(row, field, restore)
        print(
            "Verified no fabricated legacy submissions, active unclassified legacy files, and populated submission/archive/category/revision downgrade protection."
        )

        with Session(scratch) as db, db.begin():
            first_report_station = db.scalar(select(Station).limit(1))
            first_report_station_id = first_report_station.id
            first_report_station.source_first_reported_at = now()
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            first_report_station = db.get(Station, first_report_station_id)
            assert first_report_station.source_first_reported_at is not None
            assert first_report_station.source_first_connected_at is None
            first_report_station.source_first_reported_at = None
        print(
            "Verified historical unknown first reporting time and populated timestamp downgrade protection."
        )

        for model, object_id, key, value in [
            (Equipment, legacy_device_id, "equipment_type", "v2g"),
            (Equipment, legacy_device_id, "model_name", "Device downgrade proof"),
            (Equipment, legacy_device_id, "source_unique_code", "SOURCE-ONLY"),
            (Equipment, legacy_device_id, "manufactured_on", date(2024, 2, 29)),
            (Connector, legacy_connector_id, "name", "Terminal proof"),
            (Connector, legacy_connector_id, "interface_type", "Source interface"),
        ]:
            with Session(scratch) as db, db.begin():
                setattr(db.get(model, object_id), key, value)
            downgrade_refused()
            with Session(scratch) as db, db.begin():
                row = db.get(model, object_id)
                assert getattr(row, key) == value
                setattr(row, key, None)
        for field, choices in [
            ("usage_type", ["car_sharing", "community_shared", "workplace", "private_shared"]),
            ("construction_site", ["scenic_area", "bus_depot", "fuel_station", "taxi_site"]),
        ]:
            for value in choices:
                with Session(scratch) as db, db.begin():
                    row = db.scalar(select(Station).limit(1))
                    row_id = row.id
                    setattr(row, field, value)
                downgrade_refused()
                with Session(scratch) as db, db.begin():
                    row = db.get(Station, row_id)
                    assert getattr(row, field) == value
                    setattr(row, field, "unknown")
        print(
            "Verified legacy NULL device profiles and downgrade protection for every source field and expanded classification."
        )

        with Session(scratch) as db, db.begin():
            facility_station = db.scalar(select(Station).limit(1))
            facility_station_id = facility_station.id
            facility_station.independent_connection = False
            facility_station.parking_spaces = 0
            facility_station.supply_capacity = "0.000"
            facility_station.supply_capacity_unit = "unknown"
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            facility_station = db.get(Station, facility_station_id)
            assert facility_station.independent_connection is False
            assert facility_station.parking_spaces == 0
            assert facility_station.supply_capacity == 0
            for key in FIELDS:
                setattr(facility_station, key, None)
        print("Verified source facility false/zero preservation and populated downgrade refusal.")

        from app.models import ArticleReply

        with Session(scratch) as db, db.begin():
            protected_article = Article(
                kind="notice",
                title="Reply downgrade guard",
                body="Guard",
                audience="operator",
                created_by=actor.id,
                is_demo=True,
            )
            db.add(protected_article)
            db.flush()
            protected_reply = ArticleReply(
                article_id=protected_article.id,
                operator_id=db.scalar(select(Operator.id).limit(1)),
                actor_id=actor.id,
                body="Guard reply",
                rich_document={
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Guard reply"}]}
                    ],
                },
            )
            db.add(protected_reply)
            db.flush()
            guard_article_id, guard_reply_id = protected_article.id, protected_reply.id
        downgrade_refused()
        with Session(scratch) as db, db.begin():
            assert db.get(ArticleReply, guard_reply_id).rich_document["type"] == "doc"
            db.delete(db.get(ArticleReply, guard_reply_id))
            db.flush()
            db.delete(db.get(Article, guard_article_id))
        print("Verified populated structured reply downgrade refusal without data loss.")

        with scratch.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO alert_notification_preferences (category,in_app_enabled,sms_enabled,version,updated_by,updated_at) VALUES ('device_charging',false,true,1,:uid,now())"
                ),
                {"uid": actor.id},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("DELETE FROM alert_notification_preferences WHERE category='device_charging'")
            )
            protected_recipient = connection.scalar(
                text(
                    "INSERT INTO alert_recipients (name,phone,categories,enabled,deleted,version,created_by,updated_by,created_at,updated_at) VALUES ('Guard recipient','18800000000','[\"device_charging\"]',false,false,1,:uid,:uid,now(),now()) RETURNING id"
                ),
                {"uid": actor.id},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("DELETE FROM alert_recipients WHERE id=:id"), {"id": protected_recipient}
            )
            notification_profile = connection.scalar(
                text(
                    "INSERT INTO role_profiles (name,base_role,permissions,version) VALUES ('Guard notification grant','regulator','[\"alerts.read\",\"alerts.notifications\"]',1) RETURNING id"
                )
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("DELETE FROM role_profiles WHERE id=:id"), {"id": notification_profile}
            )

        with scratch.begin() as connection:
            observation_rule = connection.scalar(
                text(
                    "INSERT INTO alert_rules (code,version,name,metric,unit,trigger_value,recover_value,notifications_enabled,is_demo,enabled) VALUES ('GUARD-OBS',1,'Guard observation','demo','C',60,50,true,true,true) RETURNING id"
                )
            )
            protected_source = connection.scalar(
                text(
                    "INSERT INTO configured_alert_observations (namespace,request_key,fingerprint,station_id,operator_id,rule_id,created_at,evidence,result) SELECT 'guard','00000000-0000-0000-0000-000000000001',repeat('a',64),id,operator_id,:rid,now(),'{}','{}' FROM stations WHERE id=:sid RETURNING id"
                ),
                {"rid": observation_rule, "sid": station_id},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("DELETE FROM configured_alert_observations WHERE id=:id"),
                {"id": protected_source},
            )
            protected_event = connection.scalar(
                text(
                    "INSERT INTO alert_events (rule_id,station_id,operator_id,target,status,opened_at,last_observed_at,occurrences,last_value,rule_snapshot,acknowledge_note,version,is_demo,configuration_evidence) SELECT :rid,id,operator_id,'guard','active',now(),now(),1,0,'{}','',1,true,'{}' FROM stations WHERE id=:sid RETURNING id"
                ),
                {"rid": observation_rule, "sid": station_id},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("DELETE FROM alert_events WHERE id=:id"), {"id": protected_event}
            )
            connection.execute(
                text("DELETE FROM alert_rules WHERE id=:id"), {"id": observation_rule}
            )

        with scratch.begin() as connection:
            protected_alert = connection.scalar(
                text(
                    "INSERT INTO alert_rules (code,version,name,metric,unit,trigger_value,recover_value,notifications_enabled,is_demo,enabled,configuration) VALUES ('GUARD-CONFIG',1,'Guard config','temperature','C',60,50,true,true,true,'{}') RETURNING id"
                )
            )
        downgrade_refused()
        with scratch.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT configuration FROM alert_rules WHERE id=:id"),
                    {"id": protected_alert},
                )
                == {}
            )
            connection.execute(
                text("UPDATE alert_rules SET configuration=NULL,revision=2 WHERE id=:id"),
                {"id": protected_alert},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("DELETE FROM alert_rules WHERE id=:id"), {"id": protected_alert}
            )

        with scratch.begin() as connection:
            protected_scope = connection.scalar(
                text(
                    "INSERT INTO subsidy_device_scopes (station_id,operator_id,source,external_id,day,equipment_external_ids,complete,source_updated_at,synced_at,version,is_demo) SELECT id,operator_id,source,'protected-scope','2026-11-01','[]',true,now(),now(),1,true FROM stations WHERE id=:sid RETURNING id"
                ),
                {"sid": station_id},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM subsidy_device_scopes WHERE id=:id"),
                    {"id": protected_scope},
                )
                == 1
            )
            connection.execute(
                text("DELETE FROM subsidy_device_scopes WHERE id=:id"), {"id": protected_scope}
            )

        with scratch.begin() as connection:
            protected_equipment = connection.scalar(
                text(
                    "INSERT INTO equipment (station_id,external_id,name,power_kw) VALUES (:sid,'guard-eq','Guard equipment',0) RETURNING id"
                ),
                {"sid": station_id},
            )
            protected_observation = connection.scalar(
                text(
                    "INSERT INTO subsidy_device_days (station_id,operator_id,equipment_id,source,external_id,equipment_external_id,day,equipment_type,business_kind,energy_complete,metric_basis,source_updated_at,synced_at,version,is_demo) SELECT :sid,operator_id,:eid,source,'protected-day','guard-eq','2026-11-01','unknown','unknown',false,'unconfirmed',now(),now(),1,true FROM stations WHERE id=:sid RETURNING id"
                ),
                {"sid": station_id, "eid": protected_equipment},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            assert "subsidy_device_days" in inspect(connection).get_table_names()
            connection.execute(
                text("DELETE FROM subsidy_device_days WHERE id=:id"), {"id": protected_observation}
            )
            connection.execute(
                text("DELETE FROM equipment WHERE id=:id"), {"id": protected_equipment}
            )

        with scratch.begin() as connection:
            protected_policy = connection.scalar(
                text(
                    "INSERT INTO subsidy_policies (code,version,name,rate,effective_from,effective_to,enabled,is_demo,created_at,device_rules) VALUES ('GUARD-DEVICE',1,'Guard device',0.1,'2026-01-01','2026-12-31',true,true,now(),CAST(:rules AS json)) RETURNING id"
                ),
                {"rules": json.dumps({"configuration_version": 1})},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            assert "device_rules" in {
                column["name"] for column in inspect(connection).get_columns("subsidy_policies")
            }
            connection.execute(
                text("DELETE FROM subsidy_policies WHERE id=:id"), {"id": protected_policy}
            )

        with scratch.begin() as connection:
            protected_draft = connection.scalar(
                text(
                    "INSERT INTO rating_standard_drafts (created_by,updated_by,version,status,data,created_at,updated_at) VALUES (:uid,:uid,1,'draft','{}',now(),now()) RETURNING id"
                ),
                {"uid": actor.id},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("DELETE FROM rating_standard_drafts WHERE id=:id"), {"id": protected_draft}
            )
            connection.execute(
                text("UPDATE rating_standards SET revision=2 WHERE id=:id"), {"id": standard_id}
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("UPDATE rating_standards SET revision=1, archived=true WHERE id=:id"),
                {"id": standard_id},
            )
        downgrade_refused()
        with scratch.begin() as connection:
            connection.execute(
                text("UPDATE rating_standards SET archived=false WHERE id=:id"), {"id": standard_id}
            )
        subprocess.run(["uv", "run", "alembic", "downgrade", "0043"], cwd=root, env=env, check=True)
        subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=root, env=env, check=True)
        print(
            "Verified protected draft/revision/archive downgrade refusal and safe empty-control round trip."
        )
        print(
            "Verified migrations to current head, timestamp-based legacy result selection, and unknown legacy validity/reports."
        )
    finally:
        scratch.dispose()
        if created:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            print("Temporary verification schema removed.")


if __name__ == "__main__":
    main()
