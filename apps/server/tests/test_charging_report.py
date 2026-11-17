import io
import secrets
import zipfile
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import delete, select
from test_order_summary import login, record

from app import orders
from app.db import SessionLocal
from app.models import Audit, Equipment, Station, SubsidyClaim, SubsidyPolicy
from app.routers import charging_report as router

BASE = "/api/v1/statistics/charging-subsidy"


@pytest.fixture()
def data(accounts):
    _, _, ids = accounts
    key = "combined-" + secrets.token_hex(6)
    policy_id = None
    eqid = None
    try:
        with SessionLocal.begin() as db:
            own, other = [db.get(Station, sid) for sid in ids]
            own.name, other.name = key, key + "other"
            device = Equipment(station_id=own.id, external_id="A", name="=SUM(1,2)", power_kw=1)
            db.add(device)
            db.flush()
            eqid = device.id
            policy = SubsidyPolicy(
                code=key,
                version=1,
                name="Fixture",
                rate=1,
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 12, 31),
            )
            db.add(policy)
            db.flush()
            policy_id = policy.id
            for station, flag, amount, status, start in [
                (own, True, "1.01", "approved", date(2026, 11, 1)),
                (own, True, "2.02", "disbursed", date(2026, 11, 1)),
                (own, False, "3.03", "approved", date(2026, 11, 1)),
                (own, True, "100", "approved", date(2026, 10, 1)),
                (other, True, "900", "disbursed", date(2026, 11, 1)),
            ]:
                db.add(
                    SubsidyClaim(
                        station_id=station.id,
                        operator_id=station.operator_id,
                        policy_id=policy.id,
                        start_date=start,
                        end_date=date(2026, 11, 2),
                        status=status,
                        total_kwh=1,
                        base_amount=Decimal(amount),
                        adjusted_amount=Decimal(amount),
                        snapshot={},
                        is_demo=flag,
                    )
                )
            orders.ingest(
                db,
                "test",
                own.operator_id,
                own,
                [
                    record("known"),
                    record("missing", amount=None, kwh=None, duration_seconds=None, currency=None),
                    record("unmatched", equipment_external_id="not-in-inventory"),
                ],
                is_demo=True,
            )
        yield {"q": key, "period_date": "2026-11-15", "period": "month"}, eqid
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(SubsidyClaim).where(SubsidyClaim.policy_id == policy_id))
            db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id == policy_id))
            db.execute(delete(Equipment).where(Equipment.id == eqid))


def test_combined_report_keeps_sources_flags_missing_metrics_cross_period_and_device_identity(
    data, accounts, staff
):
    query, eqid = data
    with (
        login(accounts[0][0], accounts[1]) as owner,
        login(accounts[0][1], accounts[1]) as other,
        login(staff[0][1], accounts[1]) as association,
    ):
        response = owner.get(BASE, params=query)
        assert response.status_code == 200
        result = response.json()
        assert result["selected_station_count"] == 1 and result["cross_period_claim_count"] == 1
        assert result["legacy_claim_count"] == 3 and result["end_date"] == "2026-11-30"
        assert result["orders"][0]["amount"]["missing_orders"] == 1
        assert Decimal(result["orders"][0]["amount"]["value"]) == Decimal("0.20")
        by_flag = {row["is_demo"]: row for row in result["subsidies"]}
        assert Decimal(by_flag[True]["adjusted_amount"]) == Decimal("3.03")
        assert Decimal(by_flag[True]["disbursed_amount"]) == Decimal("2.02")
        assert Decimal(by_flag[False]["adjusted_amount"]) == Decimal("3.03")
        assert association.get(BASE, params=query).status_code == 403
        assert other.get(BASE, params=query).json()["orders"] == []
        equipment = owner.get(
            BASE, params={**query, "dimension": "equipment", "page_size": 1}
        ).json()
        assert equipment["total"] == 2 and equipment["unmatched_order_groups"] == 1
        assert equipment["items"][0]["equipment_id"] == eqid
        assert equipment["items"][0]["subsidies"] == []
        assert equipment["station_subsidies"] and equipment["subsidies"] == result["subsidies"]
        page2 = owner.get(
            BASE, params={**query, "dimension": "equipment", "page_size": 1, "page": 2}
        ).json()
        assert (
            page2["items"][0]["kind"] == "source_reference"
            and page2["items"][0]["equipment_id"] is None
        )
        assert page2["orders"] == equipment["orders"]
        assert (
            owner.get(
                BASE, params={**query, "period": "month", "period_date": "2024-02-23"}
            ).json()["end_date"]
            == "2024-02-29"
        )


def test_export_is_real_excel_with_literal_names_and_unallocated_money_not_duplicated(
    data, accounts, tmp_path
):
    query, _ = data
    with SessionLocal.begin() as db:
        large = db.scalar(
            select(SubsidyClaim)
            .join(Station)
            .where(Station.name == query["q"], SubsidyClaim.is_demo.is_(False))
        )
        large.base_amount = large.adjusted_amount = Decimal("1234567890123456.78")
    with login(accounts[0][0], accounts[1]) as owner:
        response = owner.get(BASE + "/export", params={**query, "dimension": "equipment"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"] == router.charging_report_excel.MEDIA
        (tmp_path / "financial-report.xlsx").write_bytes(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            assert [item.attrib["name"] for item in workbook.findall("s:sheets/s:sheet", ns)] == [
                "统计汇总",
                "充电业务",
                "补贴核算",
                "场站补贴",
            ]
            shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            texts = ["".join(node.itertext()) for node in shared.findall("s:si", ns)]
            assert "=SUM(1,2)" in texts
            assert "1234567890123456.78" in texts
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            formats = {
                item.get("numFmtId"): item.get("formatCode")
                for item in styles.findall("s:numFmts/s:numFmt", ns)
            }
            cell_formats = styles.find("s:cellXfs", ns)
            exact_cells = 0
            for path in ["xl/worksheets/sheet1.xml", "xl/worksheets/sheet4.xml"]:
                for cell in ET.fromstring(archive.read(path)).findall(".//s:c", ns):
                    value = cell.find("s:v", ns)
                    if (
                        cell.get("t") == "s"
                        and value is not None
                        and texts[int(value.text)] == "1234567890123456.78"
                    ):
                        assert formats[cell_formats[int(cell.get("s"))].get("numFmtId")] == "@"
                        exact_cells += 1
            assert exact_cells == 4
            for path in archive.namelist():
                if path.startswith("xl/worksheets/sheet") and path.endswith(".xml"):
                    assert not ET.fromstring(archive.read(path)).findall(".//s:f", ns)
            # Legacy station money has no device allocation: the equipment subsidy sheet has no data rows.
            rows = ET.fromstring(archive.read("xl/worksheets/sheet3.xml")).findall(
                "s:sheetData/s:row", ns
            )
            assert all(int(row.attrib["r"]) <= 8 for row in rows)


def test_failed_file_generation_leaves_no_success_audit_and_retry_works(
    data, accounts, monkeypatch
):
    query, _ = data
    with login(accounts[0][0], accounts[1]) as owner:
        with SessionLocal() as db:
            before = set(
                db.scalars(
                    select(Audit.id).where(Audit.action == "statistics.charging_subsidy.export")
                )
            )

        def fail(*args):
            raise ValueError("broken workbook")

        with monkeypatch.context() as patch:
            patch.setattr(router.charging_report_excel, "workbook", fail)
            with pytest.raises(ValueError, match="broken workbook"):
                owner.get(BASE + "/export", params=query)
        with SessionLocal() as db:
            assert (
                set(
                    db.scalars(
                        select(Audit.id).where(Audit.action == "statistics.charging_subsidy.export")
                    )
                )
                == before
            )
        assert owner.get(BASE + "/export", params=query).status_code == 200


def test_report_uses_one_snapshot_and_export_requires_independent_order_grant(data, accounts):
    from sqlalchemy import event

    from app.db import engine
    from app.models import RoleProfile, User

    query, _ = data
    fired = False

    def after_inventory(connection, cursor, statement, parameters, context, many):
        nonlocal fired
        if (
            fired
            or "FROM stations JOIN operators" not in statement
            or "ORDER BY stations.id" not in statement
        ):
            return
        fired = True
        with SessionLocal.begin() as db:
            claim = db.scalar(
                select(SubsidyClaim)
                .join(Station)
                .where(Station.name == query["q"], SubsidyClaim.status == "disbursed")
            )
            claim.adjusted_amount = Decimal("99.00")

    with login(accounts[0][0], accounts[1]) as owner:
        event.listen(engine, "after_cursor_execute", after_inventory)
        try:
            first = owner.get(BASE, params=query).json()
            assert fired
            assert Decimal(
                next(item for item in first["subsidies"] if item["is_demo"])["disbursed_amount"]
            ) == Decimal("2.02")
        finally:
            event.remove(engine, "after_cursor_execute", after_inventory)
        current = owner.get(BASE, params=query).json()
        assert Decimal(
            next(item for item in current["subsidies"] if item["is_demo"])["disbursed_amount"]
        ) == Decimal("99.00")
        with SessionLocal.begin() as db:
            profile = RoleProfile(
                name="report-reader-" + secrets.token_hex(6),
                base_role="operator",
                permissions=[
                    "stations.read",
                    "statistics.read",
                    "statistics.export",
                    "orders.read",
                    "subsidy.read",
                ],
                version=1,
            )
            db.add(profile)
            db.flush()
            pid = profile.id
            user = db.scalar(select(User).where(User.username == accounts[0][0]))
            uid = user.id
            user.role_profile_id = pid
        try:
            assert owner.get(BASE, params=query).status_code == 200
            assert owner.get(BASE + "/export", params=query).status_code == 403
        finally:
            with SessionLocal.begin() as db:
                db.get(User, uid).role_profile_id = None
                db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


def test_real_10000_inventory_rows_export_without_dropping_unknowns_and_10001_rejected(
    data, accounts
):
    from sqlalchemy import insert

    query, _ = data
    extra = []
    try:
        with SessionLocal.begin() as db:
            sid = db.scalar(select(Station.id).where(Station.name == query["q"]))
            extra = list(
                db.scalars(
                    insert(Equipment).returning(Equipment.id),
                    [
                        {
                            "station_id": sid,
                            "external_id": "limit-" + str(index),
                            "name": "Current fixture",
                            "power_kw": 1,
                        }
                        for index in range(9999)
                    ],
                )
            )
        selection = {**query, "period_date": "2024-02-01", "dimension": "equipment", "page_size": 1}
        with login(accounts[0][0], accounts[1]) as owner:
            report = owner.get(BASE, params=selection)
            assert report.status_code == 200 and report.json()["total"] == 10000
            exported = owner.get(BASE + "/export", params=selection)
            assert exported.status_code == 200
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                rows = ET.fromstring(archive.read("xl/worksheets/sheet2.xml")).findall(
                    "s:sheetData/s:row", ns
                )
                assert len([row for row in rows if int(row.attrib["r"]) > 8]) == 10000
            with SessionLocal.begin() as db:
                row = Equipment(station_id=sid, external_id="limit-extra", name="Extra", power_kw=1)
                db.add(row)
                db.flush()
                extra.append(row.id)
                audits = set(
                    db.scalars(
                        select(Audit.id).where(Audit.action == "statistics.charging_subsidy.export")
                    )
                )
            assert owner.get(BASE + "/export", params=selection).status_code == 422
            with SessionLocal() as db:
                assert (
                    set(
                        db.scalars(
                            select(Audit.id).where(
                                Audit.action == "statistics.charging_subsidy.export"
                            )
                        )
                    )
                    == audits
                )
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Equipment).where(Equipment.id.in_(extra)))
