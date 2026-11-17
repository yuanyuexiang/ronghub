import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from test_configured_alerts import body, submit
from test_configured_alerts import setup as setup  # noqa: PLC0414 -- re-export pytest fixture

from app import alert_notifications
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    AlertEvent,
    AlertNotice,
    AlertNotificationPreference,
    AlertNotificationRouting,
    AlertRecipient,
    AlertRule,
    AlertSmsDelivery,
    Audit,
    RoleProfile,
    User,
    VerifiedPhone,
)


@pytest.fixture()
def notify(setup, staff, accounts, monkeypatch):
    data = setup
    monkeypatch.setattr(settings, "sms_mode", "demo")
    original = {}
    with SessionLocal.begin() as db:
        alert_notifications.lock(db, write=True)
        for category in ["device_charging", "station_metric"]:
            row = db.get(AlertNotificationPreference, category)
            original[category] = (
                {
                    column.name: getattr(row, column.name)
                    for column in AlertNotificationPreference.__table__.columns
                }
                if row
                else None
            )
            if row:
                db.delete(row)
    client = TestClient(app, client=("notify-" + secrets.token_hex(6), 1))
    login = client.post(
        "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
    )
    assert login.status_code == 200
    headers = {"x-csrf-token": login.json()["csrf_token"]}
    try:
        yield data, client, headers
    finally:
        client.close()
        with SessionLocal.begin() as db:
            alert_notifications.lock(db, write=True)
            routes = select(AlertNotificationRouting.id).where(
                AlertNotificationRouting.event_id.in_(
                    select(AlertEvent.id).where(AlertEvent.rule_id.in_(data["rules"]))
                )
            )
            db.execute(delete(AlertSmsDelivery).where(AlertSmsDelivery.routing_id.in_(routes)))
            db.execute(
                delete(AlertNotificationRouting).where(
                    AlertNotificationRouting.event_id.in_(
                        select(AlertEvent.id).where(AlertEvent.rule_id.in_(data["rules"]))
                    )
                )
            )
            db.execute(delete(AlertRecipient).where(AlertRecipient.created_by == data["user"]))
            db.execute(
                delete(AlertNotificationPreference).where(
                    AlertNotificationPreference.category.in_(original)
                )
            )
            db.execute(
                delete(Audit).where(
                    Audit.user_id == data["user"], Audit.action.like("alerts.notification.%")
                )
            )
            for previous in original.values():
                if previous:
                    db.add(AlertNotificationPreference(**previous))


def recipient(name="Receiver", category="device_charging"):
    return {
        "name": name,
        "phone": "188" + str(secrets.randbelow(10**8)).zfill(8),
        "categories": [category],
        "enabled": True,
    }


def pref(client, headers, category="device_charging", **changes):
    current = next(
        row
        for row in client.get("/api/v1/alerts/notifications/preferences").json()["items"]
        if row["category"] == category
    )
    response = client.put(
        "/api/v1/alerts/notifications/preferences/" + category,
        headers=headers,
        json={
            "version": current["version"],
            "in_app_enabled": True,
            "sms_enabled": True,
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_preferences_and_recipients_versions_private_data_and_identity_separation(notify):
    data, client, headers = notify
    prefs = client.get("/api/v1/alerts/notifications/preferences")
    assert prefs.status_code == 200 and len(prefs.json()["items"]) == 6
    assert prefs.headers["cache-control"] == "no-store"
    current = next(row for row in prefs.json()["items"] if row["category"] == "device_charging")
    assert (
        current["legacy_behavior"]
        and current["in_app_enabled"] is None
        and not current["sms_enabled"]
    )
    configured = pref(client, headers, in_app_enabled=False)
    assert configured["version"] == 1 and not configured["legacy_behavior"]
    assert (
        client.put(
            "/api/v1/alerts/notifications/preferences/device_charging",
            headers=headers,
            json={"version": 0, "in_app_enabled": True, "sms_enabled": False},
        ).status_code
        == 409
    )
    raw = recipient()
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(VerifiedPhone))
    response = client.post("/api/v1/alerts/notifications/recipients", headers=headers, json=raw)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["masked_phone"] != raw["phone"]
    assert (
        client.post(
            "/api/v1/alerts/notifications/recipients", headers=headers, json=raw
        ).status_code
        == 409
    )
    updated = client.patch(
        f"/api/v1/alerts/notifications/recipients/{item['id']}",
        headers=headers,
        json={**raw, "version": 1, "enabled": False},
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert (
        client.patch(
            f"/api/v1/alerts/notifications/recipients/{item['id']}",
            headers=headers,
            json={**raw, "version": 1},
        ).status_code
        == 409
    )
    deleted = client.delete(
        f"/api/v1/alerts/notifications/recipients/{item['id']}?version=2", headers=headers
    )
    assert deleted.status_code == 200 and deleted.json()["deleted"]
    assert (
        client.get("/api/v1/alerts/notifications/recipients", params={"q": raw["phone"]}).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.post(
            "/api/v1/alerts/notifications/recipients", headers=headers, json=raw
        ).status_code
        == 201
    )
    with SessionLocal() as db:
        audits = list(
            db.scalars(
                select(Audit).where(
                    Audit.user_id == data["user"], Audit.action.like("alerts.notification.%")
                )
            )
        )
        assert all(
            raw["phone"] not in json.dumps(row.detail) and raw["name"] not in json.dumps(row.detail)
            for row in audits
        )
        assert db.scalar(select(func.count()).select_from(VerifiedPhone)) == before


def test_routing_separates_channels_freezes_versions_and_never_repeats_receipts(notify):
    data, client, headers = notify
    raw = recipient()
    first_recipient = client.post(
        "/api/v1/alerts/notifications/recipients", headers=headers, json=raw
    ).json()
    # A different category and a disabled receiver must not receive this event.
    client.post(
        "/api/v1/alerts/notifications/recipients",
        headers=headers,
        json=recipient(category="station_metric"),
    )
    client.post(
        "/api/v1/alerts/notifications/recipients",
        headers=headers,
        json={**recipient(), "enabled": False},
    )
    pref(client, headers, in_app_enabled=False)
    sample = body(data)
    first = submit(data, sample)
    assert submit(data, sample) == first
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(AlertSmsDelivery)
                .join(AlertNotificationRouting)
                .where(AlertNotificationRouting.event_id == first.event_id)
            )
        )
        assert (
            len(rows) == 1
            and rows[0].status == "simulated"
            and rows[0].phone_snapshot == raw["phone"]
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AlertNotice)
                .where(AlertNotice.event_id == first.event_id)
            )
            == 0
        )
    newraw = recipient(name="Updated receiver")
    changed = client.patch(
        f"/api/v1/alerts/notifications/recipients/{first_recipient['id']}",
        headers=headers,
        json={**newraw, "version": 1},
    )
    assert changed.status_code == 200
    pref(client, headers, in_app_enabled=True)
    submit(data, body(data, value="80", observed_at="2026-11-01T08:01:00+08:00"))
    submit(
        data,
        body(data, value=None, coverage_complete=False, observed_at="2026-11-01T08:02:00+08:00"),
    )
    with SessionLocal() as db:
        routes = list(
            db.scalars(
                select(AlertNotificationRouting)
                .where(AlertNotificationRouting.event_id == first.event_id)
                .order_by(AlertNotificationRouting.id)
            )
        )
        assert (
            len(routes) == 2
            and routes[0].preference_version == 1
            and routes[1].preference_version == 2
        )
        deliveries = list(
            db.scalars(
                select(AlertSmsDelivery)
                .where(AlertSmsDelivery.recipient_id == first_recipient["id"])
                .order_by(AlertSmsDelivery.id)
            )
        )
        assert [row.phone_snapshot for row in deliveries] == [raw["phone"], newraw["phone"]]
        assert [row.recipient_version for row in deliveries] == [1, 2]
    private = client.get(f"/api/v1/alerts/notifications/deliveries?event_id={first.event_id}")
    assert private.status_code == 200 and private.json()["total"] == 2
    assert raw["phone"] not in private.text and newraw["phone"] not in private.text
    event = client.get(f"/api/v1/alerts/{first.event_id}")
    assert raw["phone"] not in event.text and newraw["phone"] not in event.text


def test_disabled_provider_and_no_recipients_are_explicit_not_fake_success(notify, monkeypatch):
    data, client, headers = notify
    pref(client, headers)
    first = submit(data, body(data))
    with SessionLocal() as db:
        row = db.scalar(
            select(AlertNotificationRouting).where(
                AlertNotificationRouting.event_id == first.event_id
            )
        )
        assert row.sms_status == "no_recipients"
    client.post("/api/v1/alerts/notifications/recipients", headers=headers, json=recipient())
    monkeypatch.setattr(settings, "sms_mode", "disabled")
    submit(data, body(data, value="80", observed_at="2026-11-01T08:01:00+08:00"))
    with SessionLocal() as db:
        delivery = db.scalar(
            select(AlertSmsDelivery)
            .join(AlertNotificationRouting)
            .where(AlertNotificationRouting.event_id == first.event_id)
        )
        assert delivery.status == "provider_disabled"


def test_custom_roles_and_regional_staff_cannot_read_private_phones(notify, accounts, staff):
    data, _, _ = notify
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="rules-only-" + secrets.token_hex(6),
            base_role="regulator",
            permissions=["alerts.read", "alerts.rules"],
            version=1,
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        db.get(User, data["user"]).role_profile_id = pid
    try:
        for username in [staff[0][0], accounts[0][0], staff[0][1]]:
            with TestClient(app, client=("private-notify-" + secrets.token_hex(6), 1)) as client:
                login = client.post(
                    "/api/v1/auth/login", json={"username": username, "password": accounts[1]}
                )
                assert login.status_code == 200
                for suffix in ["recipients", "preferences", "routings", "deliveries"]:
                    assert client.get("/api/v1/alerts/notifications/" + suffix).status_code == 403
    finally:
        with SessionLocal.begin() as db:
            user = db.get(User, data["user"])
            user.role_profile_id = None
            user.allowed_districts = []
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))
    with TestClient(app, client=("regional-notify-" + secrets.token_hex(6), 1)) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
        )
        assert client.get("/api/v1/alerts/notifications/recipients").status_code == 403


def test_legacy_bundle_does_not_grant_new_phone_management():
    from app.authorization import normalize_grants

    grants = normalize_grants("regulator", ["alerts.read", "alerts.write"])
    assert "alerts.notifications" not in grants
    assert {"alerts.rules", "alerts.samples", "alerts.acknowledge"}.issubset(grants)


def test_unclassified_legacy_metric_never_guesses_a_sms_type_from_name(notify):
    data, client, headers = notify
    client.post("/api/v1/alerts/notifications/recipients", headers=headers, json=recipient())
    pref(client, headers)
    created = client.post(
        "/api/v1/alerts/rules",
        headers=headers,
        json={
            "code": "unmapped-" + secrets.token_hex(6),
            "version": 1,
            "name": "设备过温",
            "metric": "unmapped_custom",
            "unit": "C",
            "trigger_value": "60",
            "recover_value": "50",
        },
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    data["rules"].append(rid)
    result = client.post(
        "/api/v1/alerts/samples",
        headers=headers,
        json={
            "station_id": data["station"],
            "rule_id": rid,
            "target": "custom-demo",
            "value": "80",
            "observed_at": "2026-11-01T08:00:00+08:00",
        },
    )
    assert result.status_code == 200
    with SessionLocal() as db:
        route = db.scalar(
            select(AlertNotificationRouting).where(
                AlertNotificationRouting.event_id == result.json()["event_id"]
            )
        )
        assert (
            route.category is None and not route.sms_enabled and route.sms_status == "not_requested"
        )


def test_sms_does_not_depend_on_the_old_in_app_rule_switch(notify):
    data, client, headers = notify
    client.post("/api/v1/alerts/notifications/recipients", headers=headers, json=recipient())
    pref(client, headers)
    with SessionLocal.begin() as db:
        db.get(AlertRule, data["rules"][0]).notifications_enabled = False
    result = submit(data, body(data))
    with SessionLocal() as db:
        route = db.scalar(
            select(AlertNotificationRouting).where(
                AlertNotificationRouting.event_id == result.event_id
            )
        )
        assert route.sms_status == "simulated" and not route.in_app_enabled


def test_recipient_projection_failure_is_atomic_and_errors_do_not_echo_phone(notify, monkeypatch):
    from pydantic import ValidationError

    from app.routers import alert_notifications as router

    data, client, headers = notify
    raw = recipient()
    original = router.recipient_output

    def bad(row):
        value = original(row).model_dump()
        value["phone"] = 123
        return router.RecipientResponse.model_validate(value)

    monkeypatch.setattr(router, "recipient_output", bad)
    with pytest.raises(ValidationError):
        client.post("/api/v1/alerts/notifications/recipients", headers=headers, json=raw)
    with SessionLocal() as db:
        assert (
            db.scalar(select(AlertRecipient.id).where(AlertRecipient.phone == raw["phone"])) is None
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(
                    Audit.user_id == data["user"],
                    Audit.action == "alerts.notification.recipient.create",
                )
            )
            == 0
        )
    invalid = client.post(
        "/api/v1/alerts/notifications/recipients",
        headers=headers,
        json={**raw, "phone": "138PRIVATE!"},
    )
    assert invalid.status_code == 422 and "138PRIVATE!" not in invalid.text


def test_concurrent_preference_versions_are_serialized_without_overwriting(notify, staff, accounts):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _data, client, headers = notify
    current = pref(client, headers)
    barrier = Barrier(2)

    def update(_):
        with TestClient(app, client=("notify-cas-" + secrets.token_hex(6), 1)) as peer:
            auth = peer.post(
                "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
            )
            assert auth.status_code == 200
            barrier.wait(timeout=10)
            return peer.put(
                "/api/v1/alerts/notifications/preferences/device_charging",
                headers={"x-csrf-token": auth.json()["csrf_token"]},
                json={"version": current["version"], "in_app_enabled": False, "sms_enabled": False},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, range(2))) == [200, 409]
    with SessionLocal() as db:
        assert db.get(AlertNotificationPreference, "device_charging").version == 2
