import secrets

import pytest
from sqlalchemy import func, select

from app import presentation_seed as presentation
from app.config import settings
from app.db import SessionLocal
from app.models import (
    AlertEvent,
    Article,
    Audit,
    ChargingOrder,
    Connector,
    EnergyRecord,
    Equipment,
    InspectionTask,
    RatingApplication,
    RatingAttachment,
    RuntimeInterval,
    Station,
    SubsidyAttachment,
    SubsidyClaim,
)


def test_presentation_seed_is_owned_repeatable_and_keeps_synthetic_provenance(
    accounts, staff, tmp_path, monkeypatch
):
    names, _, station_ids = accounts
    namespace = "ronghub-showcase-" + secrets.token_hex(5)
    actor_names = {
        "regulator": staff[0][0],
        "association": staff[0][1],
        "operator_a": names[0],
        "operator_b": names[1],
    }
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    stamp = presentation.now()
    monkeypatch.setattr(presentation, "now", lambda: stamp)
    paths = []
    with SessionLocal() as db:
        try:
            before = [db.get(Station, s).name for s in station_ids]
            result = presentation.seed(
                db, namespace=namespace, actor_names=actor_names, publish_portal=True, paths=paths
            )
            db.flush()
            station_scope = select(Station.id).where(Station.source == namespace)
            assert result["stations"] == 24 and result["synthetic"] is True
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Equipment)
                    .where(Equipment.station_id.in_(station_scope))
                )
                == 96
            )
            equipment_scope = select(Equipment.id).where(Equipment.station_id.in_(station_scope))
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Connector)
                    .where(Connector.equipment_id.in_(equipment_scope))
                )
                == 192
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(EnergyRecord)
                    .where(EnergyRecord.source == namespace)
                )
                >= 540
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ChargingOrder)
                    .where(ChargingOrder.source == namespace, ChargingOrder.is_demo.is_(False))
                )
                == 0
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RuntimeInterval)
                    .where(RuntimeInterval.source == namespace, RuntimeInterval.is_demo.is_(False))
                )
                == 0
            )
            assert {"active", "recovered"} <= set(
                db.scalars(
                    select(AlertEvent.status).where(AlertEvent.station_id.in_(station_scope))
                )
            )
            rating_statuses = set(
                db.scalars(
                    select(RatingApplication.status).where(
                        RatingApplication.station_id.in_(station_scope)
                    )
                )
            )
            assert {"draft", "submitted", "reviewed", "published", "rejected"} <= rating_statuses
            claim_statuses = set(
                db.scalars(
                    select(SubsidyClaim.status).where(SubsidyClaim.station_id.in_(station_scope))
                )
            )
            assert {"draft", "submitted", "approved", "disbursed", "rejected"} <= claim_statuses
            assert set(
                db.scalars(
                    select(InspectionTask.status).where(
                        InspectionTask.station_id.in_(station_scope)
                    )
                )
            ) == {"assigned", "replied", "closed"}
            public_ids = select(Audit.object_id).where(
                Audit.action == "presentation.content",
                Audit.detail["namespace"].as_string() == namespace,
            )
            articles = list(db.scalars(select(Article).where(Article.id.in_(public_ids))))
            assert len(articles) == 17 and sum(a.portal_public for a in articles) == 13
            assert all(a.is_demo and a.status == "published" for a in articles)
            assert all(
                not a.portal_public and a.recipient_ids for a in articles if a.kind == "notice"
            )
            for model in [RatingAttachment, SubsidyAttachment]:
                records = db.scalars(
                    select(model).where(
                        model.uploaded_by.in_(
                            [
                                u.id
                                for u in db.query(presentation.User).filter(
                                    presentation.User.username.in_(names)
                                )
                            ]
                        )
                    )
                )
                for record in records:
                    from app.material_archive import read_material

                    assert len(read_material(tmp_path, record)) == record.size
            counts = [
                db.scalar(select(func.count()).select_from(model))
                for model in [
                    Station,
                    Equipment,
                    ChargingOrder,
                    Article,
                    RatingApplication,
                    SubsidyClaim,
                ]
            ]
            files = len(paths)
            presentation.seed(
                db, namespace=namespace, actor_names=actor_names, publish_portal=True, paths=paths
            )
            db.flush()
            assert counts == [
                db.scalar(select(func.count()).select_from(model))
                for model in [
                    Station,
                    Equipment,
                    ChargingOrder,
                    Article,
                    RatingApplication,
                    SubsidyClaim,
                ]
            ]
            assert len(paths) == files
            assert [db.get(Station, s).name for s in station_ids] == before
        finally:
            db.rollback()
            for path in paths:
                path.unlink(missing_ok=True)


def test_presentation_seed_refuses_production_and_unknown_namespace(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(ValueError, match="DEMO_MODE"):
        presentation.seed(None)
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(ValueError, match="namespace"):
        presentation.seed(None, namespace="demo")
