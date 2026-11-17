import csv
import io
import secrets
from datetime import UTC, datetime, timedelta

import pytest
import test_inspection_contracts as inspection_cases
from sqlalchemy import delete, event, func, select

from app.db import SessionLocal, engine
from app.models import Audit, InspectionAction, InspectionTask, RoleProfile, Station, User, now
from app.routers import inspections

case = inspection_cases.case


def listing(c, actor=0, **params):
    return c["clients"][actor].get("/api/v1/inspections", params=params)


def export(c, actor=0, **params):
    return c["clients"][actor].get("/api/v1/inspections/export", params=params)


def rows(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_creation_range_uses_beijing_midnight_and_invalid_dates_are_public_422(case):
    c = case
    prefix = "date-boundary-" + str(c["task"]["id"])
    ids = []
    with SessionLocal.begin() as db:
        original = db.get(InspectionTask, c["task"]["id"])
        for stamp in [
            datetime(2026, 11, 14, 15, 59, 59, tzinfo=UTC),
            datetime(2026, 11, 14, 16, tzinfo=UTC),
            datetime(2026, 11, 15, 16, tzinfo=UTC),
        ]:
            row = InspectionTask(
                station_id=original.station_id,
                operator_id=original.operator_id,
                title=prefix,
                description="date boundary",
                severity="normal",
                due_at=None,
                created_by=original.created_by,
                created_at=stamp,
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
    params = {"q": prefix, "created_from": "2026-11-15", "created_to": "2026-11-15"}
    result = listing(c, **params).json()
    assert result["total"] == 1 and result["items"][0]["id"] == ids[1]
    report = rows(export(c, **params))
    assert len(report) == 1 and report[0]["任务编号"] == str(ids[1])
    assert report[0]["创建时间（北京时间）"] == "2026-11-15T00:00:00+08:00"
    assert report[0]["任务期限（北京时间）"] == ""
    for bad in [
        {"created_from": "2026-11-16", "created_to": "2026-11-15"},
        {"created_to": "9999-12-31"},
        {"created_from": "invalid-date"},
        {"execution_state": "closed"},
    ]:
        assert listing(c, **bad).status_code == 422
        assert export(c, **bad).status_code == 422


def test_recent_reply_uses_actual_time_not_largest_id_and_never_backfills_its_missing_facts(case):
    c = case
    identifier = c["task"]["id"]
    later = datetime(2026, 11, 15, 16, tzinfo=UTC)
    with SessionLocal.begin() as db:
        db.get(InspectionTask, identifier).status = "returned"
        actor = db.scalar(select(User.id).where(User.operator_id == c["task"]["operator_id"]))
        first = InspectionAction(
            task_id=identifier,
            actor_id=actor,
            action="reply",
            comment="actual later reply",
            created_at=later,
            performed_at=later - timedelta(days=1),
            contact_name="Later executor",
            contact_phone="13800000001",
        )
        db.add(first)
        db.flush()
        first_id = first.id
        db.add(
            InspectionAction(
                task_id=identifier,
                actor_id=actor,
                action="reply",
                comment="imported older clock",
                created_at=later - timedelta(hours=1),
                performed_at=later - timedelta(days=2),
            )
        )
    data = listing(c, record_id=identifier, execution_state="reported").json()
    assert data["total"] == 1 and data["items"][0]["last_reply_id"] == first_id
    assert datetime.fromisoformat(data["items"][0]["last_reply_submitted_at"]) == later
    assert datetime.fromisoformat(
        data["items"][0]["last_reported_performed_at"]
    ) == later - timedelta(days=1)
    report = rows(export(c, record_id=identifier))[0]
    assert (
        report["反馈状态"] == "已反馈"
        and report["办理状态"] == "退回补充"
        and report["该次执行联系人"] == "Later executor"
    )
    with SessionLocal.begin() as db:
        newest = InspectionAction(
            task_id=identifier,
            actor_id=actor,
            action="reply",
            comment="new record with unknown execution time",
            created_at=later + timedelta(hours=1),
        )
        db.add(newest)
        db.flush()
        newest_id = newest.id
    item = listing(c, record_id=identifier).json()["items"][0]
    assert item["last_reply_id"] == newest_id and item["last_reported_performed_at"] is None
    report = rows(export(c, record_id=identifier))[0]
    assert report["该次填报执行时间（北京时间）"] == "" and report["该次执行联系人"] == "暂无数据"
    assert report["最近反馈提交时间（北京时间）"] == "2026-11-16T01:00:00+08:00"


def test_missing_legacy_reply_is_unknown_instead_of_execution_completion(case):
    c = case
    identifier = c["task"]["id"]
    assert listing(c, record_id=identifier, execution_state="not_reported").json()["total"] == 1
    with SessionLocal.begin() as db:
        db.get(InspectionTask, identifier).status = "closed"
    item = listing(c, record_id=identifier, execution_state="unknown").json()["items"][0]
    assert (
        item["execution_state"] == "unknown"
        and item["last_reply_id"] is None
        and item["last_reply_submitted_at"] is None
    )
    assert listing(c, record_id=identifier, execution_state="reported").json()["total"] == 0
    assert rows(export(c, record_id=identifier))[0]["反馈状态"] == "历史未留存"


def test_export_is_full_applied_query_not_a_page_and_refuses_overflow_without_truncation(
    case, monkeypatch
):
    c = case
    prefix = "full-query-" + str(c["task"]["id"])
    with SessionLocal.begin() as db:
        original = db.get(InspectionTask, c["task"]["id"])
        for index in range(25):
            db.add(
                InspectionTask(
                    station_id=original.station_id,
                    operator_id=original.operator_id,
                    title=prefix,
                    description=str(index),
                    severity="major",
                    due_at=None,
                    created_by=original.created_by,
                )
            )
    result = listing(
        c, actor=1, q=prefix, severity="major", overdue=False, page_size=5, page=2
    ).json()
    assert result["total"] == 25 and len(result["items"]) == 5
    report = rows(export(c, actor=1, q=prefix, severity="major", overdue=False))
    assert len(report) == 25 and len({row["任务编号"] for row in report}) == 25
    before = 0
    with SessionLocal() as db:
        before = db.scalar(
            select(func.count())
            .select_from(Audit)
            .where(
                Audit.action == "inspections.export",
                Audit.user_id == db.get(InspectionTask, c["task"]["id"]).created_by,
            )
        )
    monkeypatch.setattr(inspections, "EXPORT_LIMIT", 3)
    rejected = export(c, q=prefix)
    assert rejected.status_code == 422 and "未截断" in rejected.json()["detail"]
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(
                    Audit.action == "inspections.export",
                    Audit.user_id == db.get(InspectionTask, c["task"]["id"]).created_by,
                )
            )
            == before
        )


@pytest.mark.parametrize(
    "grants,read_status,export_status",
    [
        (["inspections.read"], 200, 403),
        (["statistics.export"], 403, 403),
        (["inspections.read", "statistics.export"], 200, 200),
    ],
)
def test_independent_read_and_export_permissions(case, grants, read_status, export_status):
    c = case
    with SessionLocal.begin() as db:
        user = db.get(User, db.get(InspectionTask, c["task"]["id"]).created_by)
        profile = RoleProfile(
            name="query-export-" + secrets.token_hex(8), base_role="regulator", permissions=grants
        )
        db.add(profile)
        db.flush()
        profile_id = profile.id
        user_id = user.id
        user.role_profile_id = profile.id
    try:
        assert listing(c, record_id=c["task"]["id"]).status_code == read_status
        result = export(c, record_id=c["task"]["id"])
        assert result.status_code == export_status
        if export_status == 200:
            assert len(rows(result)) == 1
    finally:
        with SessionLocal.begin() as db:
            db.get(User, user_id).role_profile_id = None
            db.flush()
            db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))


def test_export_cannot_escape_operator_or_district_scope_and_association_is_denied(case):
    c = case
    identifier = c["task"]["id"]
    assert len(rows(export(c, actor=1, record_id=identifier))) == 1
    assert rows(export(c, actor=2, record_id=identifier)) == []
    assert export(c, actor=3, record_id=identifier).status_code == 403
    with SessionLocal.begin() as db:
        db.get(User, db.get(InspectionTask, identifier).created_by).allowed_districts = []
    assert (
        listing(c, record_id=identifier).json()["total"] == 0
        and rows(export(c, record_id=identifier)) == []
    )


def test_csv_escapes_all_user_cells_and_has_no_per_record_lazy_queries(case):
    c = case
    identifier = c["task"]["id"]
    with SessionLocal.begin() as db:
        task = db.get(InspectionTask, identifier)
        task.title = "=1+1"
        task.description = "@requirement"
        db.get(Station, task.station_id).name = "\t=HYPERLINK(test)"
        actor = db.scalar(select(User.id).where(User.operator_id == task.operator_id))
        db.add(
            InspectionAction(
                task_id=identifier,
                actor_id=actor,
                action="reply",
                comment="+feedback",
                contact_name="=executor",
                contact_phone="-123",
                created_at=now(),
            )
        )
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and "FROM inspection_tasks" in statement:
            statements.append(statement)

    event.listen(engine, "after_cursor_execute", capture)
    try:
        result = export(c, record_id=identifier)
    finally:
        event.remove(engine, "after_cursor_execute", capture)
    report = rows(result)[0]
    assert report["任务标题"] == "'=1+1" and report["场站（当前名称）"] == "'\t=HYPERLINK(test)"
    assert (
        report["任务要求"] == "'@requirement"
        and report["该次执行联系人"] == "'=executor"
        and report["该次联系方式"] == "'-123"
        and report["该次反馈正文"] == "'+feedback"
    )
    assert len(statements) == 1


def test_count_rows_and_reply_facts_share_one_postgres_snapshot(case):
    c = case
    identifier = c["task"]["id"]
    fired = False

    def change(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if fired or "count(" not in statement or "FROM inspection_tasks" not in statement:
            return
        fired = True
        with SessionLocal.begin() as db:
            task = db.get(InspectionTask, identifier)
            task.status = "replied"
            actor = db.scalar(select(User.id).where(User.operator_id == task.operator_id))
            db.add(
                InspectionAction(
                    task_id=identifier,
                    actor_id=actor,
                    action="reply",
                    comment="concurrent actual report",
                    created_at=now(),
                )
            )

    event.listen(engine, "after_cursor_execute", change)
    try:
        first = listing(c, record_id=identifier, execution_state="not_reported").json()
    finally:
        event.remove(engine, "after_cursor_execute", change)
    assert (
        fired
        and first["total"] == 1
        and len(first["items"]) == 1
        and first["items"][0]["execution_state"] == "not_reported"
    )
    second = listing(c, record_id=identifier, execution_state="reported").json()
    assert second["total"] == 1 and second["items"][0]["last_reply_submitted_at"] is not None
