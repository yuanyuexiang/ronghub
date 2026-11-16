"""Local demo only: verify full migration chain and legacy rating upgrade in a temporary schema.

Run: uv run python -m app.migration_verify (from apps/server).
The current application schema is never migrated or dropped by this command.
"""

import json
import os
import secrets
import subprocess
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import settings
from app.db import engine
from app.models import Operator, RatingApplication, Station, now


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
                for station in db.scalars(select(Station))
            )
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
